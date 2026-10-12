"""Private ntfy archive HTTP service and restricted SSH collector."""
import hmac
import json
import logging
import os
import subprocess
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from store import Store

STATIC=Path(__file__).parent/'static'


def handler(store, api_token, ui_token, origins):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def respond(self, status, body, content_type='application/json'):
            raw=json.dumps(body).encode() if content_type=='application/json' else body
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Referrer-Policy','same-origin')
            self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            if self.command!='HEAD':self.wfile.write(raw)

        def role(self):
            supplied=self.headers.get('Authorization','')
            if hmac.compare_digest(supplied,'Bearer '+ui_token):return 'ui'
            if hmac.compare_digest(supplied,'Bearer '+api_token):return 'api'
            return None

        def do_GET(self):
            parsed=urlsplit(self.path)
            if parsed.path=='/health':return self.respond(200,{'status':'ok'})
            role=self.role()
            if role is None:return self.respond(401,{'error':'Authentication required'})
            try:
                if parsed.path=='/api/status':return self.respond(200,store.status())
                if parsed.path=='/api/topics':
                    topics=store.topics()
                    if role=='api':topics=[t for t in topics if t['ai_enabled']]
                    return self.respond(200,{'topics':topics})
                if parsed.path=='/api/messages':
                    query=parse_qs(parsed.query)
                    if set(query)-{'topic','q','since','limit','offset'}:raise ValueError('Unknown query parameter')
                    values={key:query.get(key,[''])[0] for key in ('topic','q')}
                    for key,default in [('since',0),('limit',100),('offset',0)]:values[key]=int(query.get(key,[default])[0])
                    if role=='api' and values['limit']>100:raise ValueError('AI requests are limited to 100 messages per page')
                    return self.respond(200,store.messages(**values,ai=role=='api'))
                if role=='ui' and parsed.path in ('/','/index.html','/app.js','/style.css','/icon.svg'):
                    name={'/':'index.html'}.get(parsed.path,parsed.path.lstrip('/'))
                    content_type={'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','style.css':'text/css; charset=utf-8','icon.svg':'image/svg+xml'}[name]
                    return self.respond(200,(STATIC/name).read_bytes(),content_type)
                return self.respond(404,{'error':'Not found'})
            except ValueError as exc:return self.respond(400,{'error':str(exc)})
            except Exception:
                logging.exception('Request failed')
                return self.respond(500,{'error':'Archive request failed'})

        def do_HEAD(self):self.do_GET()

        def do_PUT(self):
            if self.role()!='ui':return self.respond(403,{'error':'Browser access required'})
            if self.headers.get('Origin') not in origins:return self.respond(403,{'error':'Origin rejected'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.respond(415,{'error':'Use application/json'})
            parsed=urlsplit(self.path)
            if not parsed.path.startswith('/api/topics/'):return self.respond(404,{'error':'Not found'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8192:raise ValueError('Invalid body size')
                changes=json.loads(self.rfile.read(length))
                store.update_topic(unquote(parsed.path.removeprefix('/api/topics/')),changes)
                return self.respond(200,{'saved':True})
            except (ValueError,UnicodeError) as exc:return self.respond(400,{'error':str(exc)})
            except KeyError:return self.respond(404,{'error':'Unknown topic'})
            except Exception:
                logging.exception('Topic update failed')
                return self.respond(500,{'error':'Topic update failed'})
    return Handler


def collect(store, stop):
    command=['ssh','-F','/dev/null','-o','BatchMode=yes','-o','IdentityAgent=none',
        '-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
        '-o','UserKnownHostsFile=/run/source/known_hosts','-o','ConnectTimeout=10',
        '-o','ServerAliveInterval=10','-o','ServerAliveCountMax=2',
        '-i','/run/source/id_ed25519','-p',os.environ.get('NTFY_SOURCE_PORT','22'),
        os.environ['NTFY_SOURCE_USER']+'@'+os.environ['NTFY_SOURCE_HOST'],'ntfy-export-v1']
    while not stop.is_set():
        try:
            store.expire()
            result=subprocess.run(command,capture_output=True,timeout=45,check=False)
            if result.returncode:raise RuntimeError('SSH source export failed (exit '+str(result.returncode)+')')
            if len(result.stdout)>32*1024*1024:raise ValueError('Source snapshot exceeds 32 MiB')
            store.ingest(json.loads(result.stdout))
        except Exception as exc:
            # SSH stderr could contain operational details; keep it out of user/API output.
            error=str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__
            store.record_error(error)
            logging.warning('Collection failed: %s',error)
        stop.wait(30)


if __name__=='__main__':
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    api=os.environ['NTFY_API_TOKEN'];ui=os.environ['NTFY_UI_TOKEN']
    if len(api)<32 or len(ui)<32 or api==ui:raise RuntimeError('Use distinct API and UI secrets of at least 32 characters')
    store=Store(os.environ.get('NTFY_ARCHIVE_DB','/data/archive.db'),retention_days=90,source_url=os.environ.get('NTFY_BASE_URL',''))
    store.expire()
    stop=threading.Event()
    threading.Thread(target=collect,args=(store,stop),daemon=True).start()
    server=ThreadingHTTPServer(('0.0.0.0',8092),handler(store,api,ui,set(os.environ['NTFY_UI_ORIGINS'].split(','))))
    def shutdown(signum,frame):
        stop.set()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown)
    server.serve_forever()
    server.server_close()
