import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

# The app uses a temporary database for each test, avoiding classroom data changes.
tmp = tempfile.TemporaryDirectory()
os.environ['GAME_DB_PATH'] = os.path.join(tmp.name, 'test.sqlite3')
import main

class TestGame(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver')
        count=8
        data={'questionCount':count,'teamCount':3,'retries':1,
              'teamNames':['사과','배','귤'],
              'questions':[{'text':f'문제 번호는 {i+1}?','type':'text','hint':f'힌트 {i+1}','answer':'yes'} for i in range(count)],
              'specials':[{'square':4,'text':'특별 카드입니다.','answer':'몰라'}],
              'sounds':{'enabled':{k:True for k in main.SOUND_KEYS},'volume':.4}}
        r=await self.client.post('/api/rooms',json={'data':data})
        assert r.status_code == 200, r.text
        self.code=r.json()['code'];self.key=r.json()['teacherKey'];self.admin={'X-Teacher-Key':self.key}
        self.pins=[x['pin'] for x in r.json()['state']['teams']]

    async def asyncTearDown(self):
        await self.client.aclose()

    async def run_action(self, a, extra=None, admin=False, team_id=0):
        d={'action':a}
        if extra:d.update(extra)
        if not admin:d.update({'teamId':team_id,'pin':self.pins[team_id]})
        return await self.client.post(f'/api/rooms/{self.code}/action',json={'data':d},headers=self.admin if admin else {})

    async def test_safety_hints_and_answers(self):
        s=(await self.client.get('/api/rooms/'+self.code)).json()
        self.assertNotIn('pin',s['teams'][0]);self.assertNotIn('adminSpecials',s)
        self.assertEqual((await self.run_action('roll')).status_code,400)
        await self.run_action('start',admin=True)
        self.assertEqual((await self.run_action('roll',team_id=1)).status_code,403)
        with patch.object(main.random,'randint',side_effect=[2,3]),patch.object(main.random,'choice',return_value='−'):
            r=await self.run_action('roll')
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['state']['phase'],'question')
        self.assertEqual(r.json()['state']['teams'][0]['position'],1)
        student=(await self.client.get('/api/rooms/'+self.code)).json()
        self.assertNotIn('answer',student['current']);self.assertNotIn('hint',student['current'])
        r=await self.run_action('hint')
        self.assertEqual(r.json()['state']['teams'][0]['hintsUsed'],1)
        student=(await self.client.get('/api/rooms/'+self.code)).json()
        self.assertIn('hint',student['current'])
        self.assertEqual((await self.run_action('hint')).status_code,400)
        r=await self.run_action('answer',{'answer':'wrong'})
        self.assertEqual(r.json()['state']['current']['attempts'],1)
        r=await self.run_action('answer',{'answer':'YES'})
        self.assertEqual(r.json()['state']['turnIndex'],1)
        self.assertEqual(r.json()['state']['teams'][0]['position'],1)
        self.assertEqual((await self.run_action('answer',{'answer':'yes'})).status_code,403)

    async def test_special_teacher_only_and_qr(self):
        await self.run_action('start',admin=True)
        with patch.object(main.random,'randint',side_effect=[2,2]),patch.object(main.random,'choice',return_value='+'):
            result=await self.run_action('roll')
        self.assertEqual(result.json()['state']['phase'],'special')
        self.assertEqual((await self.run_action('special_correct')).status_code,403)
        q=(await self.client.get('/api/rooms/'+self.code)).json()
        self.assertEqual(q['current']['kind'],'special');self.assertNotIn('answer',q['current'])
        r=await self.run_action('special_wrong',admin=True)
        self.assertEqual(r.json()['state']['teams'][0]['position'],0)
        self.assertEqual(r.json()['state']['turnIndex'],1)
        img=await self.client.get('/api/rooms/'+self.code+'/qr')
        self.assertEqual(img.status_code,200)
        self.assertEqual(img.headers['content-type'],'image/png')
        self.assertEqual(img.content[:8],b'\x89PNG\r\n\x1a\n')

    async def test_portal_reset_swap_and_finish(self):
        await self.run_action('start', admin=True)
        # 0 + 3 lands on portal A, which teleports to portal B (12).
        with patch.object(main.random,'randint',side_effect=[1,2]),patch.object(main.random,'choice',return_value='+'):
            r=await self.run_action('roll')
        st=r.json()['state']
        self.assertEqual(st['teams'][0]['position'],12)
        self.assertEqual(st['turnIndex'],1)
        self.assertEqual([x['type'] for x in st['effects'][-2:]], ['move','portal'])
        # Current team 1 lands on reset at square 6.
        with patch.object(main.random,'randint',side_effect=[3,3]),patch.object(main.random,'choice',return_value='+'):
            r=await self.run_action('roll',team_id=1)
        st=r.json()['state']
        self.assertEqual(st['teams'][1]['position'],0)
        self.assertEqual(st['turnIndex'],2)
        self.assertEqual(st['effects'][-1]['type'],'reset')
        # Place another team in front; landing on swap exchanges positions.
        raw=main.get_room(self.code)
        raw['teams'][2]['position']=0
        raw['teams'][1]['position']=13
        main.save_room(raw)
        with patch.object(main.random,'randint',side_effect=[5,4]),patch.object(main.random,'choice',return_value='+'):
            r=await self.run_action('roll',team_id=2)
        st=r.json()['state']
        self.assertEqual(st['teams'][2]['position'],13)
        self.assertEqual(st['teams'][1]['position'],9)
        self.assertEqual(st['effects'][-1]['type'],'swap')
        # A correct academic answer on the finish cell ends the game.
        raw=main.get_room(self.code)
        raw['teams'][0]['position']=14
        main.save_room(raw)
        with patch.object(main.random,'randint',side_effect=[1,1]),patch.object(main.random,'choice',return_value='+'):
            r=await self.run_action('roll')
        self.assertEqual(r.json()['state']['phase'],'question')
        r=await self.run_action('answer',{'answer':'yes'})
        self.assertEqual(r.json()['state']['status'],'finished')
        self.assertEqual(r.json()['state']['winner'],0)

    async def test_reuse_for_other_class_is_independent(self):
        # Only the original teacher may copy the settings into a new classroom.
        unauth = await self.client.post(f'/api/rooms/{self.code}/clone', json={'data':{}})
        self.assertEqual(unauth.status_code, 403)
        invalid = await self.client.post(f'/api/rooms/{self.code}/clone', json={'data':{}},
                                         headers={'X-Teacher-Key':'wrong'})
        self.assertEqual(invalid.status_code, 403)
        await self.run_action('start', admin=True)
        with patch.object(main.random,'randint',side_effect=[2,3]), patch.object(main.random,'choice',return_value='−'):
            await self.run_action('roll')
        await self.run_action('hint')
        await self.run_action('answer', {'answer':'yes'})
        before = main.get_room(self.code)
        self.assertEqual(before['teams'][0]['position'], 1)
        self.assertEqual(before['teams'][0]['hintsUsed'], 1)

        response = await self.client.post(f'/api/rooms/{self.code}/clone', json={'data':{}}, headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        clone = response.json()
        new_code = clone['code']
        self.assertNotEqual(new_code, self.code)
        self.assertNotEqual(clone['teacherKey'], self.key)
        self.assertEqual(clone['state']['status'], 'waiting')
        self.assertEqual(clone['state']['history'], [])
        self.assertEqual(clone['state']['effects'], [])
        self.assertEqual(clone['state']['turnNumber'], 1)
        self.assertEqual(clone['state']['config']['questionCount'], 8)
        self.assertEqual([t['name'] for t in clone['state']['teams']], ['사과','배','귤'])
        self.assertTrue(all(t['position']==0 and t['hintsUsed']==0 for t in clone['state']['teams']))
        fresh_room = main.get_room(new_code)
        self.assertEqual(fresh_room['config'], before['config'])
        self.assertNotEqual(fresh_room['adminToken'], before['adminToken'])
        self.assertTrue(set(t['pin'] for t in fresh_room['teams']).isdisjoint(t['pin'] for t in before['teams']))

        # Old class credentials do not grant access to the new class.
        old_pin = self.pins[0]
        no_entry = await self.client.post(f'/api/rooms/{new_code}/join',json={'data':{'teamId':0,'pin':old_pin}})
        self.assertEqual(no_entry.status_code, 403)
        no_teacher = await self.client.post(f'/api/rooms/{new_code}/action',json={'data':{'action':'start'}},headers=self.admin)
        self.assertEqual(no_teacher.status_code, 403)
        new_teacher = await self.client.post(f'/api/rooms/{new_code}/action',json={'data':{'action':'start'}},
                                             headers={'X-Teacher-Key':clone['teacherKey']})
        self.assertEqual(new_teacher.status_code, 200)
        self.assertEqual(main.get_room(self.code), before, '새 반이 기존 반의 기록을 바꾸면 안 됩니다.')

    async def test_reset_clears_records_but_preserves_settings_and_credentials(self):
        await self.run_action('start',admin=True)
        with patch.object(main.random,'randint',side_effect=[2,3]), patch.object(main.random,'choice',return_value='−'):
            await self.run_action('roll')
        await self.run_action('hint')
        await self.run_action('answer',{'answer':'yes'})
        old = main.get_room(self.code)
        self.assertGreater(len(old['history']), 0)
        self.assertGreater(old['effectSeq'], 0)
        self.assertEqual(old['teams'][0]['hintsUsed'], 1)

        unauthorized = await self.run_action('restart',team_id=1)
        self.assertEqual(unauthorized.status_code, 403)
        result = await self.run_action('restart',admin=True)
        self.assertEqual(result.status_code, 200)
        new = main.get_room(self.code)
        self.assertEqual(new['config'], old['config'])
        self.assertEqual([t['pin'] for t in new['teams']], [t['pin'] for t in old['teams']])
        self.assertEqual(new['adminToken'], old['adminToken'])
        self.assertEqual(new['history'], [])
        self.assertEqual(new['effects'], [])
        self.assertEqual(new['effectSeq'],old['effectSeq'])  # avoid missed live effects after reset
        self.assertEqual(new['status'], 'waiting')
        self.assertEqual(new['phase'], 'waiting')
        self.assertEqual(new['turnIndex'], 0)
        self.assertEqual(new['turnNumber'], 1)
        self.assertIsNone(new['winner'])
        self.assertIsNone(new['current'])
        self.assertIsNone(new['dice'])
        self.assertEqual(new['questionBag'], [])
        self.assertTrue(all(t['position']==0 and t['hintsUsed']==0 for t in new['teams']))
        # Same room, same team PIN: students can play again after teacher starts.
        joined = await self.client.post(f'/api/rooms/{self.code}/join',json={'data':{'teamId':0,'pin':self.pins[0]}})
        self.assertEqual(joined.status_code, 200)
        started = await self.run_action('start',admin=True)
        self.assertEqual(started.json()['state']['turnIndex'],0)

    async def test_initialization_and_auth(self):
        self.assertEqual((await self.client.post('/api/rooms/'+self.code+'/join',json={'data':{'teamId':1,'pin':'000000'}})).status_code,403)
        self.assertEqual((await self.client.post('/api/rooms/'+self.code+'/join',json={'data':{'teamId':1,'pin':self.pins[1]}})).status_code,200)
        t=(await self.client.get('/api/rooms/'+self.code,headers=self.admin)).json()
        self.assertIn('pin',t['teams'][1]);self.assertIn('adminSpecials',t)
        self.assertEqual(t['goal'],15)
        self.assertEqual(len(t['tiles']),4)
        s=(await self.run_action('sound',{'sounds':{'volume':.5,'enabled':{'move':False}}},admin=True)).json()['state']
        self.assertFalse(s['config']['sounds']['enabled']['move'])

if __name__ == '__main__': unittest.main()
