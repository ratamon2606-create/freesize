// Automatically reveal the Freesize app in about 3 seconds total.
// Deep links bypass this intro; internal SPA navigation never replays it.
const entrance=document.getElementById('fs-entrance');
if(entrance&&['/','/home','/home/'].includes(location.pathname)){
  document.documentElement.classList.add('fs-intro-active');
  entrance.classList.add('is-ready');
  const reduceMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;
  let opening=false;
  let finishTimer;
  let startTimer;
  function complete(){
    clearTimeout(startTimer);
    clearTimeout(finishTimer);
    entrance.remove();
    document.documentElement.classList.remove('fs-intro-active','fs-intro-leaving');
  }
  function enter(){
    if(opening)return;
    opening=true;
    document.documentElement.classList.add('fs-intro-leaving');
    entrance.classList.add('is-opening');
    // Fallback if transitionend does not fire (background tabs / reduced motion).
    finishTimer=setTimeout(complete,reduceMotion?50:1750);
  }
  startTimer=setTimeout(enter,1200);
  entrance.addEventListener('transitionend',e=>{
    if(opening&&e.target.classList.contains('fs-entrance__portal')&&e.propertyName==='transform')complete();
  });
}else entrance?.remove();
