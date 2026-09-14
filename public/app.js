const $=id=>document.getElementById(id);
let lastEvent=null, chart=null, map=null, mapMarkers=[], currentUser=null;
async function j(u,o){const r=await fetch(u,{headers:{'Content-Type':'application/json'},...o,credentials:'same-origin'});return r.json();}

async function health(){try{const h=await j('/api/health');$('health').textContent=`● ${h.mode} mode · model ${h.model_trained?'ready':'untrained'}`}catch{$('health').textContent='● server offline'}}

async function checkAuth(){
  try{
    const me=await j('/api/auth/me');
    if(!me.logged_in){
      const h=await j('/api/health');
      if(!h.google_configured){
        $('loginWall').style.display='block';$('mainApp').style.display='none';
        $('loginWall').innerHTML=`<div class="card" style="text-align:center;max-width:500px;margin:60px auto">
          <h2>Google OAuth not configured</h2>
          <p>To enable login, set these environment variables and restart the server:</p>
          <pre class="mono" style="text-align:left;font-size:13px">set GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
set GOOGLE_CLIENT_SECRET=your-client-secret
set SUPER_ADMIN_EMAIL=your-email@gmail.com
python server.py</pre>
          <p class="muted">See <code>GOOGLE_OAUTH_SETUP.md</code> for full setup guide.</p>
          <p style="margin-top:16px"><a href="/api/auth/google/login" class="login-btn">Try Google Login</a></p>
          <p class="muted" style="margin-top:8px">This will show an error until credentials are set.</p>
        </div>`;
        return false;
      }
      $('loginWall').style.display='block';$('mainApp').style.display='none';return false;
    }
    currentUser=me;
    $('loginWall').style.display='none';$('mainApp').style.display='block';
    $('loggedOut').style.display='none';$('loggedIn').style.display='flex';
    $('userName').textContent=me.name;$('userPic').src=me.picture||'';
    $('userRole').textContent=me.role;$('userRole').className='pill role-pill '+me.role;
    document.body.className='role-'+me.role;
    if(me.role==='super_admin')$('navOwner').style.display='block';
    return true;
  }catch{return false}
}

async function status(){try{const s=await j('/api/lock/status');$('lockState').textContent=s.state;$('lockState').className='big '+(s.state==='UNLOCKED'?'GRANT':'');
if(s.last_event){$('lastDecision').textContent=s.last_event.decision;$('lastDecision').className='big '+s.last_event.decision;$('lastRisk').textContent=`R=${(+s.last_event.r).toFixed(3)} (C=${(+s.last_event.c).toFixed(2)} B=${(+s.last_event.b).toFixed(2)} H=${(+s.last_event.h).toFixed(2)}) · user ${s.last_event.user_id}`;
if(currentUser&&(currentUser.role==='admin'||currentUser.role==='super_admin')&&s.last_event.city){
  $('lastLocation').innerHTML=`<b>${s.last_event.city}</b>, ${s.last_event.region}, ${s.last_event.country}<br>IP: ${s.last_event.client_ip||'—'}`;
  if(s.last_event.lat&&s.last_event.lon)showMap(s.last_event.lat,s.last_event.lon,s.last_event.city);
}};}catch{}}

function showMap(lat,lon,label){
  $('map').style.display='block';$('mapNote').style.display='block';
  if(!map){map=L.map('map').setView([lat,lon],13);L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap'}).addTo(map)}
  mapMarkers.forEach(m=>map.removeLayer(m));mapMarkers=[];
  map.setView([lat,lon],13);
  const m=L.marker([lat,lon]).addTo(map).bindPopup(label||'Last access');mapMarkers.push(m);
}

async function users(){const u=await j('/api/users');$('userSel').innerHTML=u.users.map(x=>`<option value="${x.id}">${x.name} (${x.id})</option>`).join('')}

async function config(){const c=await j('/api/config');$('policyBox').textContent=`R=${c.wC}·C+${c.wB}·B+${c.wH}·H\nτ1=${c.tau1} grant→step-up · τ2=${c.tau2} step-up→deny\ndevice=${c.device_id} mode=${c.mode}`;$('wC').value=c.wC;$('wB').value=c.wB;$('wH').value=c.wH;$('t1').value=c.tau1;$('t2').value=c.tau2}

async function events(){const e=await j('/api/events?limit=80');const isAdmin=currentUser&&(currentUser.role==='admin'||currentUser.role==='super_admin');
$('evTable').querySelector('tbody').innerHTML=e.events.map(r=>`<tr><td>${r.id}</td><td>${new Date(r.ts*1000).toLocaleTimeString()}</td><td>${r.user_id}</td><td>${r.credential_ok?'✓':'✗'}</td><td>${(+r.hour).toFixed(1)}</td><td>${(+r.c).toFixed(2)}</td><td>${(+r.b).toFixed(2)}</td><td>${(+r.h).toFixed(2)}</td><td><b>${(+r.r).toFixed(2)}</b></td><td class="${r.decision}">${r.decision}</td>${isAdmin?`<td>${r.city?r.city+', '+r.country:'—'}</td>`:''}</tr>`).join('')}

async function loadOwnerDashboard(){
  if(!currentUser||currentUser.role!=='super_admin')return;
  try{
    const d=await j('/api/super/overview');
    $('ownerStats').innerHTML=`
      <div class="card"><h3>Total Events</h3><div class="big">${d.total_events}</div></div>
      <div class="card"><h3>Total Lock Users</h3><div class="big">${d.owners.length}</div></div>
      <div class="card"><h3>Registered Locks</h3><div class="big">${d.owners.length}</div></div>`;
    $('ownerTable').querySelector('tbody').innerHTML=d.owners.map(o=>`<tr><td>${o.google_email}</td><td>${o.google_name||'—'}</td><td><code>${o.device_id}</code></td><td>${o.lock_name||'—'}</td><td>${o.registered_at?new Date(o.registered_at*1000).toLocaleDateString():'—'}</td><td>${o.is_super_admin?'Super Admin':'Admin'}</td></tr>`).join('');
    $('ownerEvents').querySelector('tbody').innerHTML=d.recent_events.map(r=>`<tr><td>${r.id}</td><td>${new Date(r.ts*1000).toLocaleTimeString()}</td><td>${r.user_id}</td><td class="${r.decision}">${r.decision}</td><td>${r.city?r.city+', '+r.country:'—'}</td><td>${r.client_ip||'—'}</td></tr>`).join('');
  }catch(e){$('ownerStats').innerHTML='<p class="muted">Failed to load owner data</p>'}
}

async function loadLocations(){
  if(!currentUser||(currentUser.role!=='admin'&&currentUser.role!=='super_admin'))return;
  try{
    const d=await j('/api/admin/locations');
    if(d.locations&&d.locations.length>0){
      const latest=d.locations[0];
      if(latest.lat&&latest.lon){showMap(latest.lat,latest.lon,latest.city||'Last access')}
    }
  }catch{}
}

document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');$('tab-'+b.dataset.tab).classList.add('active');if(b.dataset.tab==='owner')loadOwnerDashboard()});

$('btnLock').onclick=async()=>{await j('/api/lock/command',{method:'POST',body:JSON.stringify({command:'LOCK'})});status();events()};
$('btnUnlock').onclick=async()=>{await j('/api/lock/command',{method:'POST',body:JSON.stringify({command:'UNLOCK'})});status();events()};
$('btnRefresh').onclick=events;
$('btnAccess').onclick=async()=>{
 const body={userId:$('userSel').value,credential:$('cred').value,sessionNovelty:$('sn').checked?1:0};
 if($('hour').value!=='')body.hour=+$('hour').value; if($('iv').value!=='')body.interArrivalMin=+$('iv').value;
 const r=await j('/api/access/request',{method:'POST',body:JSON.stringify(body)});
 lastEvent=r.eventId;$('accessOut').textContent=JSON.stringify(r,null,2);
 $('stepupBox').style.display=r.decision==='STEP_UP'?'flex':'none';
 status();events();
};
$('btnStepup').onclick=async()=>{const r=await j('/api/access/stepup',{method:'POST',body:JSON.stringify({eventId:lastEvent,otp:$('otp').value})});$('accessOut').textContent=JSON.stringify(r,null,2);status();events()};
$('btnSave').onclick=async()=>{await j('/api/config',{method:'POST',body:JSON.stringify({wC:+$('wC').value,wB:+$('wB').value,wH:+$('wH').value,tau1:+$('t1').value,tau2:+$('t2').value})});$('saveMsg').textContent='saved ✓';config()};
$('btnSim').onclick=async()=>{$('simOut').textContent='running…';const r=await j('/api/sim/run',{method:'POST',body:'{}'});
 $('simOut').textContent=`precision=${r.precision} recall=${r.recall} F1=${r.f1} FPR=${r.fpr} FNR=${r.fnr}\nTP=${r.tp} FP=${r.fp} TN=${r.tn} FN=${r.fn}\ngrant=${r.grants} step-up=${r.stepups} deny+alert=${r.denies} (n=${r.n_test})`;
 $('anaOut').textContent=JSON.stringify(r,null,2);
 const ctx=$('ch');if(chart)chart.destroy();
 chart=new Chart(ctx,{type:'bar',data:{labels:['Precision','Recall','F1'],datasets:[{label:'Adaptive risk engine',data:[r.precision,r.recall,r.f1]}]},options:{scales:{y:{min:0,max:1}}}});
};
$('btnRegister').onclick=async()=>{const r=await j('/api/lock/register',{method:'POST',body:JSON.stringify({device_id:$('regDeviceId').value,lock_name:$('regLockName').value})});$('regMsg').textContent=r.ok?'Lock registered ✓':(r.error||'Failed')};

(async()=>{
  const ok=await checkAuth();
  if(!ok)return;
  health();status();users();config();events();loadLocations();
  setInterval(()=>{health();status()},5000);
})();
