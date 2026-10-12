"""Fixed read-only source command. Never accepts SQL, paths or arbitrary commands."""
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

TOPIC=re.compile(r'^[A-Za-z0-9_-]{1,64}$')
MAX_MESSAGES=20000


def export(cache, auth, now=None, source_url='', cache_seconds=86400):
    now=int(time.time() if now is None else now)
    topics={}
    def remember(name,evidence):
        if isinstance(name,str) and TOPIC.fullmatch(name):topics[name]={'name':name,'evidence':evidence}
    with closing(sqlite3.connect(Path(cache).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        db.row_factory=sqlite3.Row
        rows=db.execute("SELECT * FROM messages WHERE published=1 AND event='message' ORDER BY time DESC,id DESC LIMIT ?",(MAX_MESSAGES+1,)).fetchall() if 'id' in {r[1] for r in db.execute('PRAGMA table_info(messages)')} else db.execute("SELECT * FROM messages WHERE published=1 AND event='message' ORDER BY time DESC LIMIT ?",(MAX_MESSAGES+1,)).fetchall()
        messages=[]
        for row in rows[:MAX_MESSAGES]:
            item=dict(row);remember(item['topic'],'message cache')
            message={k:item[k] for k in ('topic','time','title','message','priority')}
            message.update(id=item['mid'],tags=item.get('tags','').split(',') if item.get('tags') else [],event='message')
            # URL/action payloads remain data; this service never fetches or executes them.
            for key in ('click','icon','content_type','encoding','sequence_id'):
                if item.get(key):message[key]=item[key]
            if item.get('attachment_url'):
                message['attachment']={key.removeprefix('attachment_'):item[key] for key in ('attachment_name','attachment_type','attachment_size','attachment_url','attachment_expires') if key in item}
            messages.append(message)
        for row in db.execute('SELECT DISTINCT topic FROM messages'):remember(row[0],'message cache')
    if Path(auth).exists():
        with closing(sqlite3.connect(Path(auth).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
            for row in db.execute('SELECT DISTINCT topic FROM user_access'):remember(row[0],'access rule')
            for row in db.execute('SELECT prefs FROM user'):
                prefs=json.loads(row[0] or '{}')
                for sub in prefs.get('subscriptions',[]) if isinstance(prefs,dict) else []:
                    if source_url and isinstance(sub,dict) and sub.get('base_url',sub.get('baseUrl',source_url)).rstrip('/')==source_url.rstrip('/'):
                        remember(sub.get('topic'),'account subscription')
    return {'collected_at':now,'topics':sorted(topics.values(),key=lambda t:t['name']),
        'messages':messages,'cache_seconds':cache_seconds,'truncated':len(rows)>MAX_MESSAGES}


if __name__=='__main__':
    if os.environ.get('SSH_ORIGINAL_COMMAND')!='ntfy-export-v1':
        print('Only ntfy-export-v1 is allowed',file=sys.stderr);sys.exit(1)
    try:
        print(json.dumps(export(os.environ.get('NTFY_CACHE_DB','/var/cache/ntfy/cache.db'),os.environ.get('NTFY_AUTH_DB','/var/lib/ntfy/auth.db'),source_url=os.environ.get('NTFY_BASE_URL',''),cache_seconds=int(os.environ.get('NTFY_CACHE_SECONDS','86400'))),separators=(',',':')))
    except Exception as exc:
        print('ntfy export failed: '+type(exc).__name__,file=sys.stderr);sys.exit(1)
