const $=id=>document.getElementById(id);
let currentUser=null, lastPinEvent=null;
async function j(u,o){const r=await fetch(u,{headers:{'Content-Type':'application/json'},...o,credentials:'same-origin'});return r.json();}

async function checkAuth(){
  try{
    const me=await j('/api/auth/me');
    if(!me.logged_in){$('loginWall').style.display='block';$('mainApp').style.display='none';return false;}
    currentUser=me;
    $('loginWall').style.display='none';$('mainApp').style.display='block';
    $('loggedOut').style.display='none';$('loggedIn').style.display='flex';
    $('userName').textContent=me.name;$('userPic').src=me.picture||'';
    if(me.role==='admin'||me.role==='super_admin')$('adminNote').style.display='block';
    configureAccess();
    return true;
  }catch{return false}
}

function configureAccess(){
  if(!currentUser)return;
  if(currentUser.tier===1){
    $('otpFlow').style.display='none';
    $('pinFlow').style.display='block';
    $('accessIntro').textContent='Enter your permanent PIN to unlock the door.';
  }else{
    $('otpFlow').style.display='block';
    $('pinFlow').style.display='block';
    $('accessIntro').textContent='Verify your identity with an OTP, then enter the PIN to unlock.';
  }
}

$('btnRequestOtp').onclick=async()=>{
  const r=await j('/api/user/request-otp',{method:'POST',body:JSON.stringify({phone:$('tempPhone').value})});
  if(r.ok){
    $('otpSection').style.display='block';
    $('otpSection').innerHTML=`<p class="muted">OTP sent to ${r.phone_masked}</p><label>Enter OTP <input id="tempOtp" placeholder="6-digit code"/></label><div class="row"><button id="btnVerifyOtp">Verify</button></div><p class="muted">Demo: OTP is <b>${r.demo_otp}</b></p>`;
    $('btnVerifyOtp').onclick=async()=>{
      const r2=await j('/api/user/verify-otp',{method:'POST',body:JSON.stringify({phone:$('tempPhone').value,otp:$('tempOtp').value})});
      if(r2.ok){$('pinResult').style.display='block';$('tempPinCode').textContent=r2.pin;$('otpSection').style.display='none';}
      else alert(r2.error);
    };
  } else alert(r.error);
};

$('btnPinAccess').onclick=async()=>{
  const r=await j('/api/access/pin',{method:'POST',body:JSON.stringify({userId:currentUser?currentUser.email:'',pin:$('tempPinEntry').value})});
  lastPinEvent=r.eventId;
  $('pinAccessOut').textContent=JSON.stringify(r,null,2);
  $('pinStepupBox').style.display=r.decision==='STEP_UP'?'flex':'none';
};
$('btnPinStepup').onclick=async()=>{
  const r=await j('/api/access/stepup',{method:'POST',body:JSON.stringify({eventId:lastPinEvent,otp:$('pinOtp').value})});
  $('pinAccessOut').textContent=JSON.stringify(r,null,2);
};

(async()=>{await checkAuth();})();
