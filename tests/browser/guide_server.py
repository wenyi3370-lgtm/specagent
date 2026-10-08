"""Private guide fixture; exposes only counters for explicit validation requests."""
import runpy
from app import main

runpy.run_path('tests/browser/seed_accounts.py')
app=main.app
stats={'validations':0}


@app.middleware('http')
async def count_validation(request,call_next):
    if request.method=='POST' and request.url.path=='/api/project/validate':
        stats['validations']+=1
    return await call_next(request)


@app.get('/__test/guide-stats')
def counters():
    return stats
