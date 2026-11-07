# SPDX-License-Identifier: GPL-2.0-only
"""Hash-checked source downloads with bounded transport retries and atomic cache."""
import hashlib,http.client,os,re,shutil,tempfile,time,urllib.error,urllib.request
from pathlib import Path
from urllib.parse import unquote, urlsplit

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def ubuntu_pool_urls(url):
    '''Keep snapshot selection while offering hash-identical official pool files.'''
    parsed=urlsplit(url)
    match=re.fullmatch(r'/ubuntu/\d{8}T\d{6}Z/(pool/[A-Za-z0-9/._+%~:-]+)',parsed.path)
    if (parsed.scheme!='https' or parsed.netloc!='snapshot.ubuntu.com'
            or parsed.query or parsed.fragment or not match):
        return [url]
    pool=match[1]
    decoded=unquote(pool,errors='strict')
    if (not re.fullmatch(r'pool/[A-Za-z0-9/._+~:-]+',decoded)
            or any(part in ('.','..') for part in decoded.split('/'))
            or re.search(r'%(?:2f|5c)',pool,re.I)):
        raise ValueError('Unsafe Ubuntu archive pool path')
    return [url,'https://ports.ubuntu.com/ubuntu-ports/'+pool,
            'https://archive.ubuntu.com/ubuntu/'+pool,
            'https://launchpad.net/ubuntu/+archive/primary/+files/'+pool.rsplit('/',1)[1]]


def fetch(url,expected,destination,*,cache_dir=None,opener=urllib.request.urlopen,sleep=time.sleep):
    if not re.fullmatch('[a-f0-9]{64}',expected) or not url.startswith('https://'):
        raise ValueError('Pinned SHA256 and HTTPS are required')
    destination=Path(destination)
    candidates=ubuntu_pool_urls(url)
    cache_dir=Path(cache_dir or os.environ.get('ONEPLUS13_DOWNLOAD_CACHE',Path.home()/'.cache/oneplus13-downloads'))
    cache_dir.mkdir(parents=True,exist_ok=True);cached=cache_dir/expected
    if not cached.is_file() or digest(cached)!=expected:
        for attempt in range(3):
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(dir=cache_dir,delete=False) as output:
                    temporary=Path(output.name)
                    with opener(candidates[min(attempt,len(candidates)-1)],timeout=45) as response:shutil.copyfileobj(response,output)
                if digest(temporary)!=expected:raise ValueError('Pinned source archive checksum mismatch')
                os.replace(temporary,cached);break
            except (urllib.error.URLError,http.client.HTTPException,TimeoutError,ConnectionError) as error:
                if attempt==2:raise
                print('PINNED_DOWNLOAD_TRANSPORT_RETRY attempt='+str(attempt+1)+' error='+type(error).__name__,flush=True)
                sleep(2*(attempt+1))
            finally:
                if temporary is not None:temporary.unlink(missing_ok=True)
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent,delete=False) as output:temporary=Path(output.name)
        shutil.copyfile(cached,temporary)
        os.replace(temporary,destination)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    return destination
