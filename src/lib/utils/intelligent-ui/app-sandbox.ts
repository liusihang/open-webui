import type { AppBlockProps } from './blocks';
import { isAppStateKey, isAppStateValue } from './blocks';

const tags = new Set(
	'div span p h1 h2 h3 h4 h5 h6 section article main header footer strong em b i small pre code ul ol li table thead tbody tr th td caption label input button select option textarea output progress meter canvas br hr'.split(
		' '
	)
);
const attributes = new Set(
	'id class title role aria-label aria-live aria-describedby aria-labelledby type value min max step placeholder disabled checked selected multiple rows cols for name maxlength width height'.split(
		' '
	)
);
/** Build a fresh allowlisted tree: no URLs, embedded browsing contexts, forms or handlers survive. */
export function sanitizeAppHTML(html: string): string {
	if (typeof document === 'undefined') return '';
	// A template's content is inert: unlike DOMParser documents, images/frames cannot load.
	const source = document.createElement('template');
	source.innerHTML = html;
	const target = document.implementation.createHTMLDocument('');
	let nodes = 0;
	function copy(node: Node, parent: Node, depth = 0) {
		if (++nodes > 2048 || depth > 32) return;
		if (node.nodeType === 3) {
			parent.appendChild(target.createTextNode(node.textContent ?? ''));
			return;
		}
		if (node.nodeType !== 1) return;
		const element = node as Element;
		const tag = element.tagName.toLowerCase();
		if (!tags.has(tag)) return;
		const clean = target.createElement(tag);
		for (const attr of element.attributes) {
			if (attributes.has(attr.name) && attr.value.length <= 256)
				clean.setAttribute(attr.name, attr.value);
		}
		if (
			tag === 'input' &&
			!['text', 'number', 'range', 'checkbox', 'radio', 'color'].includes(
				clean.getAttribute('type') ?? 'text'
			)
		)
			clean.setAttribute('type', 'text');
		if (tag === 'canvas') {
			clean.setAttribute(
				'width',
				String(Math.min(1200, Math.max(1, Number(clean.getAttribute('width')) || 600)))
			);
			clean.setAttribute(
				'height',
				String(Math.min(900, Math.max(1, Number(clean.getAttribute('height')) || 300)))
			);
		}
		if (tag === 'button') clean.setAttribute('type', 'button');
		parent.appendChild(clean);
		for (const child of element.childNodes) copy(child, clean, depth + 1);
	}
	for (const node of source.content.childNodes) copy(node, target.body);
	return target.body.innerHTML;
}
const json = (value: unknown) => JSON.stringify(value).replace(/</g, '\\u003c');
export function newAppToken(): string {
	return Array.from(crypto.getRandomValues(new Uint8Array(24)), (n) =>
		n.toString(16).padStart(2, '0')
	).join('');
}
export function acceptAppMessage(
	event: Pick<MessageEvent, 'source' | 'origin' | 'data'>,
	source: Window | null,
	token: string,
	keys: string[]
): { key: string; value: string | number | boolean } | null {
	if (
		!source ||
		event.source !== source ||
		event.origin !== 'null' ||
		!event.data ||
		typeof event.data !== 'object'
	)
		return null;
	const data = event.data;
	if (
		data.channel !== 'intelligent-ui-app' ||
		data.token !== token ||
		data.type !== 'write' ||
		!isAppStateKey(data.key) ||
		!keys.includes(data.key) ||
		!isAppStateValue(data.value)
	)
		return null;
	return { key: data.key, value: data.value };
}

/** Model JavaScript runs in a worker, never in the iframe DOM realm. CSP blocks worker networking;
 * the trusted iframe bootstrap exposes only text/value updates and bounded event/state messages.
 * Unlike arbitrary iframe JS, worker code cannot navigate a browsing context to exfiltrate data.
 */
export function buildAppDocument(view: AppBlockProps, token: string): string {
	const workerCode = `
const callbacks = new Map(); let shared = Object.create(null);
const emit = (command) => postMessage(command);
const ui = Object.freeze({
 getState: (key) => shared[key],
 setState: (key,value) => emit({type:'write',key,value}),
 setText: (id,text) => emit({type:'text',id,value:String(text).slice(0,16384)}),
 setValue: (id,value) => emit({type:'value',id,value:String(value).slice(0,256)}),
 draw: (id,commands) => emit({type:'draw',id,commands}),
 setStyle: (id,property,value) => emit({type:'style',id,property,value:String(value).slice(0,64)}),
 on: (id,event,callback) => { if (typeof callback === 'function') callbacks.set(id+':'+event, callback); },
 onState: (callback) => { if (typeof callback === 'function') callbacks.set('$state',callback); }
});
self.onmessage = ({data}) => {
 if (data.type === 'state') { shared = data.state; callbacks.get('$state')?.(Object.assign({}, shared)); }
 if (data.type === 'event') callbacks.get(data.id+':'+data.event)?.(data.value);
};
${view.js ?? ''}
`;
	const bootstrap = `(() => {
'use strict';
const token=${json(token)}, keys=${json(view.stateKeys ?? [])}, channel='intelligent-ui-app', parentOrigin=${json(typeof location === 'undefined' ? 'null' : location.origin)};
let worker;
try { worker = new Worker(URL.createObjectURL(new Blob([${json(workerCode)}],{type:'text/javascript'}))); }
catch { document.getElementById('app-error').hidden=false; return; }
let budget=0; setInterval(()=>budget=0,1000);
worker.onmessage=({data:d})=>{
 if (++budget > 100 || !d || typeof d !== 'object') return;
 if(d.type==='write' && keys.includes(d.key) && (typeof d.value==='boolean' || (typeof d.value==='string' && d.value.length<=256) || (typeof d.value==='number' && Number.isFinite(d.value) && Math.abs(d.value)<=1e12))) parent.postMessage({channel,token,type:'write',key:d.key,value:d.value},parentOrigin==='null'?'*':parentOrigin);
 if(d.type==='draw' && typeof d.id==='string' && Array.isArray(d.commands) && d.commands.length<=256){
  const node=document.getElementById(d.id); const ctx=node?.tagName==='CANVAS'?node.getContext('2d'):null;
  if(ctx) for(const c of d.commands){
   if(!Array.isArray(c)||c.length>8) continue;
   const num=(v)=>typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=2000;
   const color=(v)=>typeof v==='string'&&/^#[a-fA-F0-9]{3}(?:[a-fA-F0-9]{3})?$/.test(v);
   if(c[0]==='clear') ctx.clearRect(0,0,node.width,node.height);
   if(c[0]==='rect'&&c.slice(1,5).length===4&&c.slice(1,5).every(num)&&color(c[5])){ctx.fillStyle=c[5];ctx.fillRect(c[1],c[2],c[3],c[4]);}
   if(c[0]==='circle'&&c.slice(1,4).length===3&&c.slice(1,4).every(num)&&c[3]>=0&&color(c[4])){ctx.fillStyle=c[4];ctx.beginPath();ctx.arc(c[1],c[2],c[3],0,Math.PI*2);ctx.fill();}
   if(c[0]==='line'&&c.slice(1,5).length===4&&c.slice(1,5).every(num)&&color(c[5])&&num(c[6])&&c[6]>0&&c[6]<=20){ctx.strokeStyle=c[5];ctx.lineWidth=c[6];ctx.beginPath();ctx.moveTo(c[1],c[2]);ctx.lineTo(c[3],c[4]);ctx.stroke();}
   if(c[0]==='text'&&typeof c[1]==='string'&&c[1].length<=256&&num(c[2])&&num(c[3])&&color(c[4])&&num(c[5])&&c[5]>=8&&c[5]<=72){ctx.fillStyle=c[4];ctx.font=c[5]+'px system-ui';ctx.fillText(c[1],c[2],c[3]);}
  }
 }
 if(d.type==='style' && typeof d.id==='string' && typeof d.value==='string' && d.value.length<=64){
  const node=document.getElementById(d.id);
  const dimension=['width','height','minWidth','minHeight','maxWidth','maxHeight'].includes(d.property) && /^(?:[0-9]{1,3}(?:\\.[0-9]{1,2})?px|(?:100|[0-9]{1,2})(?:\\.[0-9]{1,2})?%)$/.test(d.value);
  const color=['color','backgroundColor','borderColor'].includes(d.property) && /^#[a-fA-F0-9]{3}(?:[a-fA-F0-9]{3})?$/.test(d.value);
  if(node && (dimension||color)) node.style[d.property]=d.value;
 }
 if((d.type==='text'||d.type==='value') && typeof d.id==='string' && typeof d.value==='string' && d.value.length<=16384){
  const node=document.getElementById(d.id); if(!node) return;
  if(d.type==='text') node.textContent=d.value; else if(['INPUT','SELECT','TEXTAREA','OUTPUT','PROGRESS','METER'].includes(node.tagName)) node.value=d.value.slice(0,256);
 }
};
window.addEventListener('message', e=>{
 const d=e.data; if(e.source!==parent || e.origin!==parentOrigin || !d || d.channel!==channel || d.token!==token || d.type!=='state') return;
 const state=Object.create(null); for(const key of keys){const v=d.state?.[key]; if(typeof v==='boolean'||(typeof v==='string'&&v.length<=256)||(typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=1e12)) state[key]=v;}
 worker.postMessage({type:'state',state});
});
for(const event of ['click','input','change']) document.addEventListener(event,e=>{
 const n=e.target; if(!n || !n.id) return;
 worker.postMessage({type:'event',event,id:n.id,value:n.type==='checkbox'?n.checked:String(n.value??'').slice(0,256)});
});
worker.onerror=()=>{ document.getElementById('app-error').hidden=false; };
parent.postMessage({channel,token,type:'ready'},parentOrigin==='null'?'*':parentOrigin);
})();`;
	// CSS cannot break out of its raw-text element. URL loads are denied by CSP, including CSS URLs.
	const css = (view.css ?? '').replace(/</g, '\\3c ');
	const csp = `default-src 'none'; script-src 'nonce-${token}'; style-src 'unsafe-inline'; worker-src blob:; connect-src 'none'; img-src 'none'; font-src 'none'; media-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; navigate-to 'none'`;
	return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}"><meta name="referrer" content="no-referrer"><style>body{font:14px system-ui;margin:16px;color:#172033}input,button,select,textarea{font:inherit;max-width:100%;box-sizing:border-box}button{cursor:pointer}*{box-sizing:border-box}${css}</style></head><body>${sanitizeAppHTML(view.html)}<p id="app-error" role="status" hidden>This app could not run.</p><script nonce="${token}">${bootstrap}</script></body></html>`;
}
