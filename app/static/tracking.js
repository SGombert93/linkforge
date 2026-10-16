(() => {
'use strict';
const config=JSON.parse(document.getElementById('tracking-config').textContent);
document.querySelectorAll('[data-dialog]').forEach(button=>button.addEventListener('click',()=>document.getElementById(button.dataset.dialog)?.showModal()));
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
if(config.preview){document.querySelectorAll('.service').forEach(a=>a.addEventListener('click',e=>e.preventDefault()));return;}
if(!config.meta_pixel&&!config.google_ads_id&&!config.ga4_id)return;
const banner=document.getElementById('consent');
const consentKey='linkforge:consent:v1:'+JSON.stringify([config.meta_pixel,config.google_ads_id,config.ga4_id]);
const conversionKey='linkforge:converted:'+config.release_id+':'+JSON.stringify([config.meta_pixel,config.google_ads_id,config.google_ads_label]);
let allowed=false,loaded=false,converted=false;
function get(store,key){try{return store.getItem(key);}catch{return null;}}
function set(store,key,value){try{store.setItem(key,value);}catch{}}
function addScript(src){const script=document.createElement('script');script.async=true;script.src=src;document.head.append(script);}
function load(){if(loaded)return;loaded=true;
 if(config.google_ads_id||config.ga4_id){window.dataLayer=window.dataLayer||[];window.gtag=function(){window.dataLayer.push(arguments);};gtag('consent','default',{ad_storage:'denied',analytics_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'});gtag('consent','update',{ad_storage:'granted',analytics_storage:'granted',ad_user_data:'granted',ad_personalization:'granted'});gtag('js',new Date());if(config.google_ads_id)gtag('config',config.google_ads_id);if(config.ga4_id)gtag('config',config.ga4_id);addScript('https://www.googletagmanager.com/gtag/js?id='+encodeURIComponent(config.google_ads_id||config.ga4_id));}
 if(config.meta_pixel){const fb=window.fbq=function(){fb.callMethod?fb.callMethod.apply(fb,arguments):fb.queue.push(arguments);};if(!window._fbq)window._fbq=fb;fb.push=fb;fb.loaded=true;fb.version='2.0';fb.queue=[];fbq('consent','grant');fbq('set','autoConfig',false,config.meta_pixel);fbq('init',config.meta_pixel);fbq('track','PageView');addScript('https://connect.facebook.net/en_US/fbevents.js');}
}
function clearTrackingCookies(){document.cookie.split(';').forEach(item=>{const name=item.split('=')[0].trim();if(!/^(_ga|_gid|_gat|_gcl|_fbp|_fbc)/.test(name))return;const parts=location.hostname.split('.');const domains=['',...parts.map((_,i)=>'.'+parts.slice(i).join('.'))];domains.forEach(domain=>{document.cookie=name+'=; Max-Age=0; Path=/'+(domain?'; Domain='+domain:'')+'; SameSite=Lax';});});}
function choose(value){set(localStorage,consentKey,JSON.stringify({value,time:Date.now()}));allowed=value==='accepted';banner.hidden=true;if(allowed){load();}else if(loaded){if(window.fbq)fbq('consent','revoke');if(window.gtag)gtag('consent','update',{ad_storage:'denied',analytics_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'});clearTrackingCookies();location.reload();}}
let saved;try{saved=JSON.parse(get(localStorage,consentKey)||'null');}catch{}
if(saved&&Date.now()-saved.time<180*24*60*60*1000){allowed=saved.value==='accepted';if(allowed)load();}else banner.hidden=false;
document.getElementById('accept').addEventListener('click',()=>choose('accepted'));
document.getElementById('reject').addEventListener('click',()=>choose('rejected'));
document.getElementById('cookie-settings').addEventListener('click',()=>banner.hidden=false);
document.querySelectorAll('.service').forEach(link=>link.addEventListener('click',event=>{
 if(!allowed)return;
 const parameters={release_id:config.release_id,release_slug:config.release_slug,service:link.dataset.service,link_index:Number(link.dataset.linkIndex)};
 if(config.ga4_id&&window.gtag)gtag('event','streaming_link_click',{...parameters,send_to:config.ga4_id});
 if(!converted&&!get(sessionStorage,conversionKey)){
  converted=true;set(sessionStorage,conversionKey,'1');
  if(config.meta_pixel&&window.fbq)fbq('trackCustom','StreamingLinkClick',parameters);
  if(config.google_ads_id&&window.gtag)gtag('event','conversion',{send_to:config.google_ads_id+'/'+config.google_ads_label,...parameters});
 }
 // Allow a bounded dispatch window for ordinary same-tab clicks. Never wait on an ad network.
 if(event.button===0&&!event.ctrlKey&&!event.metaKey&&!event.shiftKey&&!event.altKey){event.preventDefault();setTimeout(()=>location.assign(link.href),180);}
}));
})();
