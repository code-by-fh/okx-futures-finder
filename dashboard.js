'use strict';
const el=id=>document.getElementById(id);
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const number=(v,d=3)=>finite(v)?v.toLocaleString('de-DE',{maximumFractionDigits:d}):'–';
const price=v=>finite(v)?v.toLocaleString('de-DE',{maximumSignificantDigits:8}):'–';
const percent=(v,signed=false)=>finite(v)?(signed&&v>0?'+':'')+v.toLocaleString('de-DE',{minimumFractionDigits:2,maximumFractionDigits:3})+' %':'–';
const volume=v=>finite(v)?v.toLocaleString('de-DE',{notation:v>=1e6?'compact':'standard',maximumFractionDigits:v>=1e6?2:0}):'–';
let data={pairs:[]},sortKey='atr_pct',sortDirection=-1,connected=false,serverTime=0,receivedAt=0;
function cell(row,text,cls=''){const td=document.createElement('td');if(text!==undefined)td.textContent=text;td.className=cls;row.append(td);return td;}
function profileRows(){
    const mode=el('profile').value;
    return data.pairs.filter(p=>p.profiles?.[mode]&&(p.profiles[mode].rank||1)<=(data.profile_limit||400))
        .map(p=>({...p,...p.profiles[mode]}));
}
function filtered(){
    const query=el('search').value.trim().toUpperCase(),minVol=Math.max(0,Number(el('min-volatility').value)||0),minVolume=Math.max(0,Number(el('min-volume').value)||0);
    const maxSpread=el('max-spread').value===''?Infinity:Math.max(0,Number(el('max-spread').value)||0);
    return profileRows().filter(p=>p.symbol.toUpperCase().includes(query)&&finite(p.atr_pct)&&p.atr_pct>=minVol&&p.turnover_5m>=minVolume&&p.spread<=maxSpread).sort((a,b)=>{
        if(sortKey==='symbol')return sortDirection*a.symbol.localeCompare(b.symbol);
        const av=a[sortKey],bv=b[sortKey];if(!finite(av)||!finite(bv))return finite(av)?-1:finite(bv)?1:0;
        return sortDirection*(av-bv)||(a.rank||0)-(b.rank||0);
    });
}
function renderPairs(){
    const rows=filtered(),fragment=document.createDocumentFragment();el('visible').textContent=rows.length+' von '+profileRows().length+' Kontrakten im '+el('profile').value+'-Modus';
    for(const p of rows){
        const tr=document.createElement('tr');cell(tr,p.rank,'num');
        const name=cell(tr,p.symbol.split('-')[0],'symbol'),id=document.createElement('small');id.textContent=p.symbol;name.append(id);
        const asof=new Date(p.asof*1000).toISOString().replace('T',' ').slice(0,16)+' UTC';
        cell(tr,price(p.price)+' '+p.quote_ccy,'num').title='Schlusskurs bis '+asof;
        cell(tr,percent(p.atr_pct),'num volatility').title='ATR bis '+asof;
        cell(tr,finite(p.activity_ratio)?number(p.activity_ratio,2)+'x':'–','num').title=el('profile').value==='1m'?'Letzte 5 Minuten / ATR(14)':'Letzte 15 Minuten / ATR(14)';
        cell(tr,volume(p.turnover_5m)+' '+p.quote_ccy,'num').title=number(p.turnover_5m,2)+' '+p.quote_ccy;
        cell(tr,finite(p.turnover_60m)?volume(p.turnover_60m)+' '+p.quote_ccy:'–','num');cell(tr,percent(p.spread),'num');
        const a=document.createElement('a');a.textContent='OKX öffnen ↗';a.className='chart-link';a.href=p.url;a.target='_blank';a.rel='noopener noreferrer';a.setAttribute('aria-label',p.symbol+' bei OKX öffnen');cell(tr).append(a);fragment.append(tr);
    }
    if(!rows.length){const tr=document.createElement('tr');cell(tr,data.pairs.length?'Keine passenden Kontrakte oder noch zu wenig lückenlose Historie für diesen Zeitrahmen.':'Noch keine passenden Marktdaten. Es werden nur Kontrakte mit aktuellem Umsatz und ausreichender Historie angezeigt.','empty').colSpan=9;fragment.append(tr);}
    el('pairs').replaceChildren(fragment);
}
function updateStatus(){
    if(!connected)return;
    const age=data.volume_end_ts?Math.max(0,serverTime+(Date.now()-receivedAt)/1000-data.volume_end_ts):null;
    const stale=age!==null&&age>Math.max(120,(data.refresh_seconds||60)*2),failures=data.ranking_errors||0,error=data.errors||0;
    el('freshness').textContent=age===null?'Noch kein Messfenster':'Fensterende vor '+(age<60?Math.floor(age)+' s':Math.floor(age/60)+' min');
    el('status').textContent=stale?'Daten überfällig':error?'Datenfehler':failures?'Teilweise Datenfehler':data.volume_end_ts?'Finder aktiv':'Erste Messung';
    el('status').className='badge'+(stale||error||failures?' bad':'');el('warning').hidden=!(stale||error||failures);
    el('warning').textContent=stale?'Die angezeigte Liste ist älter als erwartet. Prüfe, ob der Finder noch läuft.':error?'Die Aktualisierung ist fehlgeschlagen. Die letzte Liste bleibt sichtbar; Details unten.':failures?failures+' Paare konnten nicht vollständig gemessen werden und fehlen in der Rangliste.':'';
}
function render(){
    renderPairs();updateStatus();
    el('market-label').textContent=data.market_label||'OKX KRYPTO-FUTURES';
    el('profile-description').textContent=el('profile').value==='1m'
        ? '1m-Chart: ATR(14) als Hauptmaß (rund 14 Minuten), letzte 5 Minuten als Aktivitätsvergleich.'
        : '5m-Chart: ATR(14) als Hauptmaß (rund 70 Minuten), letzte 15 Minuten als Aktivitätsvergleich.';
    el('window').textContent=data.volume_end?'Minuten-Messfenster endet '+data.volume_end+' · '+(data.candidates||0)+' Kandidaten geprüft.':'Noch kein abgeschlossenes Messfenster vorhanden.';
    el('progress').textContent=(data.phase||'Startet')+(data.total?' · '+(data.processed||0)+' / '+data.total+' Paare verarbeitet':'');
    el('traffic').textContent=number(data.network_requests||0,0)+' API-Abfragen seit Start · '+number((data.network_bytes||0)/1e6,2)+' MB JSON-Daten · letzter Durchlauf: '+number(data.cycle_seconds||0,1)+' s';
    el('error').textContent=data.last_error?'Zuletzt protokollierter Fehler: '+data.last_error:'';
}
for(const id of ['profile','search','min-volatility','min-volume','max-spread'])el(id).addEventListener('input',renderPairs);
el('reset').addEventListener('click',()=>{for(const id of ['search','min-volatility','min-volume','max-spread'])el(id).value='';renderPairs();});
document.querySelectorAll('[data-sort]').forEach(th=>{const button=th.querySelector('button');button.dataset.label=button.textContent.replace(/ [↑↓]$/,'');button.addEventListener('click',()=>{
    if(sortKey===th.dataset.sort)sortDirection*=-1;else{sortKey=th.dataset.sort;sortDirection=['symbol','spread'].includes(sortKey)?1:-1;}
    document.querySelectorAll('[data-sort]').forEach(column=>{const active=column.dataset.sort===sortKey;column.setAttribute('aria-sort',active?(sortDirection===1?'ascending':'descending'):'none');const b=column.querySelector('button');b.textContent=b.dataset.label+(active?(sortDirection===1?' ↑':' ↓'):'');});renderPairs();
});});
async function refresh(){
    try{const response=await fetch('/api/status',{cache:'no-store',signal:AbortSignal.timeout(4000)});if(!response.ok)throw Error('HTTP '+response.status);const next=await response.json();if(!Array.isArray(next.pairs))throw Error('Ungültige Marktdaten');data=next;serverTime=next.server_time||Date.now()/1000;receivedAt=Date.now();connected=true;render();el('updated').textContent='Ansicht aktualisiert '+new Date().toLocaleTimeString('de-DE')+' · alle 5 s';}
    catch(error){connected=false;el('status').textContent='Finder nicht erreichbar';el('status').className='badge bad';el('warning').hidden=false;el('warning').textContent='Verbindung zum Finder unterbrochen. Bereits angezeigte Werte bleiben als alte Daten sichtbar.';}
    finally{setTimeout(refresh,5000);}
}
refresh();setInterval(updateStatus,1000);

el('profile').addEventListener('change',render);
