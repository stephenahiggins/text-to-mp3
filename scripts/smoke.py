"""Exercise real local ingestion, queue, synthesis, MP3 encoding and metadata."""
import json
from pathlib import Path
import subprocess
import time
import httpx
BASE='http://127.0.0.1:8765/api/'
SAMPLE='The river path was quiet this morning. A robin watched from the garden wall as sunlight reached the water.\n\nI stopped beside the bridge, put away my phone, and listened to the town waking up. Sometimes a short walk is enough to make the day feel new.'
with httpx.Client(timeout=60) as client:
    response=client.post(BASE+'extract',json={'text':SAMPLE,'title':'A Quiet Morning — local test'})
    response.raise_for_status()
    article=response.json()
    article.update(author='Original test narration',publication='Article Voice')
    response=client.post(BASE+'jobs',json={'article':article,'voice':'bm_george','speed':1})
    response.raise_for_status()
    job_id=response.json()['id']
    start=time.monotonic()
    while time.monotonic()-start<300:
        job=next(j for j in client.get(BASE+'jobs').json() if j['id']==job_id)
        print(job['status'],job['completed'],job['total'],flush=True)
        if job['status']=='failed':raise RuntimeError(job['error'])
        if job['status']=='completed':break
        time.sleep(2)
    else:raise TimeoutError('Synthesis exceeded five minutes')
    print('End-to-end seconds:', round(time.monotonic()-start,2))
    print(subprocess.check_output(['ffprobe','-v','quiet','-show_format','-show_streams','-of','json',job['output']]).decode())
