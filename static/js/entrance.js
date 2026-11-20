// A single page-load intro; internal SPA navigation never replays it.
const entrance=document.getElementById('fs-entrance');
if(entrance&&['/','/home','/home/'].includes(location.pathname)){
  document.documentElement.classList.add('fs-intro-active');
  entrance.classList.add('is-ready');
  const trigger=entrance.querySelector('.fs-entrance__trigger');
  let opening=false;
  let completeTimer;
  function complete(){
    clearTimeout(completeTimer);
    document.removeEventListener('keydown',keydown);
    entrance.remove();
    document.documentElement.classList.remove('fs-intro-active','fs-intro-leaving');
  }
  function enter(){
    if(opening)return;
    opening=true;
    document.documentElement.classList.add('fs-intro-leaving');
    entrance.classList.add('is-opening');
    trigger.disabled=true;
    // Fade all doors and lettering out while revealing the already-loaded app.
    completeTimer=setTimeout(complete,matchMedia('(prefers-reduced-motion: reduce)').matches?100:2050);
  }
  trigger.addEventListener('click',enter);
  const keydown=e=>{
    if(e.code!=='Space'||e.repeat||opening||e.altKey||e.ctrlKey||e.metaKey)return;
    const target=e.target;
    if(target?.closest?.('input,textarea,select,button,[contenteditable="true"]')&&target!==trigger)return;
    e.preventDefault();enter();
  };
  document.addEventListener('keydown',keydown);
  entrance.addEventListener('transitionend',e=>{if(opening&&e.target.classList.contains('fs-entrance__portal'))complete()});
}else entrance?.remove();
