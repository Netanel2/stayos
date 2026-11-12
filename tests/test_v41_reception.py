"""v4.1: durable receptionist acknowledgements, owner assignment, staff scope, offsite safety."""
import sys
import sqlite3
import tempfile
import json
from pathlib import Path
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_platform import start,send
from backup import snapshot,verify_archive,restore_to,perform


def run():
    tmp,server,base=start()
    try:
        owner=requests.Session();guest=requests.Session();url=base+'/api/h/bayview-demo/'
        send(owner,'POST',base+'/api/auth/login',json={'username':'owner','password':'SecureOwnerPass!12345'})
        assert send(owner,'GET',base+'/health')['version']=='4.1.0'
        assert send(guest,'GET',base+'/api/owner/backup-status',401)['error']
        assert send(owner,'GET',base+'/api/owner/backup-status')['auto_backup_enabled'] is False
        send(owner,'POST',url+'manage/staff',201,json={'username':'only.orders','display_name':'Food station','role':'staff','password':'WorkPass!Secure987','permissions':['edit:orders']})
        send(owner,'POST',url+'manage/staff',201,json={'username':'viewer.orders','display_name':'Food observer','role':'staff','password':'WorkPass!Secure987','permissions':['view:orders']})
        assigned=next(x['id'] for x in send(owner,'GET',url+'manage/staff') if x['username']=='only.orders')
        worker=requests.Session();viewer=requests.Session()
        send(worker,'POST',base+'/api/auth/login',json={'username':'only.orders','password':'WorkPass!Secure987'})
        send(viewer,'POST',base+'/api/auth/login',json={'username':'viewer.orders','password':'WorkPass!Secure987'})
        demo=send(owner,'POST',url+'manage/demo-pass',json={});token=demo['url'].split('token=')[-1]
        product=send(guest,'GET',url+'public')['menu'][0]
        booked=send(guest,'POST',url+'guest/'+token+'/order',201,json={'items':[{'product_id':product['id'],'qty':1}], 'area':'pool','detail':'Sunbed 44','request_key':'e2e-ack-test-001'})
        key={'type':'order','id':booked['id']}
        events=send(owner,'GET',url+'manage/events')
        event=next(e for e in events if e['type']=='order')
        assert event['action_needed'] and not event['acknowledged_at'] and event['room']=='101'
        assert send(viewer,'POST',url+'manage/events/action',403,json={**key,'action':'acknowledge'})['error']
        assert send(guest,'POST',url+'manage/events/action',401,json={**key,'action':'claim'})['error']
        assert send(worker,'POST',url+'manage/events/action',403,json={**key,'action':'assign','assignee_id':assigned})['error']
        send(worker,'POST',url+'manage/events/action',json={**key,'action':'claim'})
        event=next(e for e in send(owner,'GET',url+'manage/events') if e['type']=='order')
        assert event['assigned_to']==assigned and not event['acknowledged_at']
        send(owner,'POST',url+'manage/events/action',json={**key,'action':'assign','assignee_id':assigned})
        ack=send(worker,'POST',url+'manage/events/action',json={**key,'action':'acknowledge'})
        assert ack['acknowledged_by']==assigned and ack['acknowledged_at']
        send(owner,'POST',url+'manage/events/action',json={**key,'action':'acknowledge'})
        event=next(e for e in send(owner,'GET',url+'manage/events') if e['type']=='order')
        assert event['acknowledged_by']==assigned and event['action_needed'] is False
        assert all(e['type']=='order' for e in send(worker,'GET',url+'manage/events'))
        assert send(owner,'POST',base+'/api/h/nonexistent/manage/events/action',404,json={**key,'action':'acknowledge'})['error']
        assert send(owner,'POST',url+'manage/events/action',404,json={'type':'order','id':989898,'action':'claim'})['error']
        assert send(owner,'POST',url+'manage/events/action',400,json={'type':'bogus','id':1,'action':'claim'})['error']
        print('PASS durable acknowledge, self-claim, scoped assignment, idempotency and tenant isolation')
        with tempfile.TemporaryDirectory() as btmp:
            root=Path(btmp);images=root/'media'/'17';images.mkdir(parents=True);(images/'photo.webp').write_bytes(b'webp-test')
            archive,info=snapshot(Path(tmp.name)/'platform.sqlite',root/'media',root/'backups')
            assert info['ok'] and info['media_count']==1
            restore_to(archive,root/'restored')
            checkdb=sqlite3.connect(root/'restored'/'platform.sqlite')
            assert checkdb.execute('SELECT COUNT(*) FROM reception_actions').fetchone()[0]>=1
            checkdb.close()
            assert (root/'restored'/'media'/'17'/'photo.webp').read_bytes()==b'webp-test'
            result=perform(Path(tmp.name)/'platform.sqlite',root/'media',root/'backups')
            assert result['status']=='success' and result['offsite_saved'] is False
            assert json.loads((root/'backups'/'status.json').read_text())['status']=='success'
            corrupt=root/'bad.zip';corrupt.write_bytes(b'bad')
            try:verify_archive(corrupt)
            except Exception:pass
            else:raise AssertionError('Corrupted archive was not rejected')
        print('PASS online SQLite backup, archive integrity, media, isolated restore and status report')
        print('RESULT: all v4.1 API and backup tests passed')
    finally:
        server.terminate();server.wait(timeout=6);tmp.cleanup()

if __name__=='__main__':run()