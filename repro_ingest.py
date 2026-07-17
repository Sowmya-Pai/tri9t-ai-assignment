import os
import fitz
import requests

p = 'mini_test.pdf'
doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 72), 'Hello from the API test')
doc.save(p)
doc.close()

with open(p, 'rb') as f:
    r = requests.post(
        'http://127.0.0.1:8000/documents/ingest',
        files={'file': (p, f, 'application/pdf')},
        data={'name': 'Mini Test'},
        timeout=30,
    )
    print('status', r.status_code)
    print(r.text)

os.remove(p)
