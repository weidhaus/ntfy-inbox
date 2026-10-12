#!/usr/bin/env python3
"""Create local tokens without displaying or overwriting existing secrets."""
import os
import secrets
from pathlib import Path

root=Path(__file__).resolve().parents[1]
path=root/'.env'
content=path.read_text() if path.exists() else (root/'.env.example').read_text()
lines=[]
for line in content.splitlines():
    key,sep,value=line.partition('=')
    if key in ('NTFY_API_TOKEN','NTFY_UI_TOKEN') and sep and not value.strip():
        line=key+'='+secrets.token_urlsafe(48)
    lines.append(line)
os.umask(0o077)
path.write_text('\n'.join(lines)+'\n')
os.chmod(path,0o600)
print('Local .env created. Set the source settings and ADMIN_PASSWORD_HASH; tokens were not printed.')
