(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), sources:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  // Some native checkbox/radio controls are intentionally transparent overlays and remain the
  // actual pointer target. Admit only that narrow click surface; never inherit ancestor opacity.
  const transparentNativeTarget = e => {
    if (e.tagName!=='INPUT' || !['checkbox','radio'].includes(e.type) ||
        getComputedStyle(e).opacity!=='0' || e.closest('[aria-hidden="true"],[inert]') ||
        !e.checkVisibility({checkOpacity:false,checkVisibilityCSS:true})) return false;
    for (let p=e.parentElement;p;p=p.parentElement)
      if (getComputedStyle(p).opacity==='0') return false;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
    return r.width>0 && r.height>0 && x>=0 && y>=0 && x<innerWidth && y<innerHeight &&
      e.contains(document.elementFromPoint(x,y));
  };
  const targetable = e => visible(e) || transparentNativeTarget(e);
  cache.targetable=targetable;
  // Returns the nearest ancestor whose overflow clips (x,y), or null if nothing clips it. The
  // caller distinguishes a scrollable clipper (offers a remedy) from a non-scrollable one (hidden/
  // clip, no remedy) using the returned element's own overflow/scrollHeight.
  const clipped = (e,x,y) => {
    for (let p=e.parentElement;p && p!==document.body;p=p.parentElement) {
      const style=getComputedStyle(p), overflow=style.overflow+' '+style.overflowX+' '+style.overflowY;
      if (!/(auto|scroll|hidden|clip)/.test(overflow)) continue;
      const r=p.getBoundingClientRect();
      if (x<r.left || x>r.right || y<r.top || y>r.bottom) return p;
    }
    return null;
  };
  const proxy = e => {
    if (visible(e)) return e;
    if (e.tagName!=='INPUT' || !['checkbox','radio'].includes(e.type)) return null;
    const labelled=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>document.getElementById(id)).filter(Boolean);
    const candidates=[...new Set([...(e.labels||[]),...labelled])].filter(visible);
    if (candidates.length===1) return candidates[0];
    return transparentNativeTarget(e) && e.getAttribute('aria-label')?.trim() ? e : null;
  };
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(document.getElementById(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const all=[...document.querySelectorAll('*')], unsupported={
    frames:all.filter(e=>['IFRAME','FRAME'].includes(e.tagName) && visible(e)).length,
    open_shadow_roots:all.filter(e=>e.shadowRoot && visible(e)).length,
    nested_scroll:all.filter(e=>e!==document.documentElement && e!==document.body && visible(e) &&
      e.scrollHeight>e.clientHeight+1 && /(auto|scroll)/.test(getComputedStyle(e).overflowY)).length,
    popup_links:all.filter(e=>e.tagName==='A' && e.href && e.target && e.target.toLowerCase()!=='_self' && visible(e)).length,
    canvases:all.filter(e=>e.tagName==='CANVAS' && visible(e)).length,
    file_inputs:all.filter(e=>e.tagName==='INPUT' && e.type==='file' && visible(e)).length,
  };
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
    }
    return null;
  };
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    [...document.querySelectorAll('input,textarea,select')].filter(safe)
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly])];
  cache.guard=(target,source=target)=>{
    if (!target?.isConnected || !source?.isConnected || !targetable(target)) return null;
    const scope=target.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || target.parentElement;
    return [identity(target),role(source),name(source),source.value??null,source.checked??null,
      source.selectedIndex??null,source.readOnly??null,source.matches(':disabled'),
      source.getAttribute('aria-disabled'),source.getAttribute('aria-expanded'),
      source.getAttribute('aria-checked'),source.getAttribute('aria-selected'),
      source.getAttribute('href'),scope?.innerText?.slice(0,6000)||''];
  };
  cache.guardNode=id=>cache.guard(cache.nodes.get(id),cache.sources.get(id));
  // ADR-0038: a target hidden only because a scrollable area (or the page) is scrolled away from it is
  // still observed; within one area height it is offered with the side it lies on, and act() scrolls it
  // into view and hit-tests it before input. Nearer ones win when there are too many.
  const revealable = (target,r) => {
    for (let p=target.parentElement;p && p!==document.body;p=p.parentElement) {
      const style=getComputedStyle(p);
      if (!/(auto|scroll)/.test(style.overflowY) || p.scrollHeight<=p.clientHeight+1) continue;
      const box=p.getBoundingClientRect();
      if (r.top>=box.bottom-r.height/2 && r.top-box.bottom<=p.clientHeight) return {side:'below',distance:r.top-box.bottom};
      if (r.bottom<=box.top+r.height/2 && box.top-r.bottom<=p.clientHeight) return {side:'above',distance:box.top-r.bottom};
      return null;
    }
    if (r.top>=innerHeight && r.top-innerHeight<=innerHeight) return {side:'below',distance:r.top-innerHeight};
    if (r.bottom<=0 && -r.bottom<=innerHeight) return {side:'above',distance:-r.bottom};
    return null;
  };
  const actions=[], clippingContainers=new Map(), hidden=[];
  for (const e of document.querySelectorAll(selector)) {
    const target=proxy(e);
    if (!safe(e) || !target || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const r=target.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=role(e);
    if (!rname || r.width<=0 || r.height<=0 || x<0 || x>=innerWidth ||
        (e.tagName==='A' && e.target && e.target.toLowerCase()!=='_self')) continue;
    const clipper=y<0 || y>=innerHeight ? null : clipped(target,x,y);
    let reveal=null;
    if (clipper) {
      // Record the nearest clipping ancestor so a remedying scroll action can be offered below,
      // instead of silently dropping a candidate that a real user could reach by scrolling it.
      const cid=identity(clipper);
      if (!clippingContainers.has(cid)) clippingContainers.set(cid,clipper);
    }
    if (clipper || y<0 || y>=innerHeight) {
      reveal=revealable(target,r);
      if (!reveal) continue;
    }
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    const node=identity(target); cache.sources.set(node,e);
    const base={node,role:rname,label:name(e)||rname,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    if (reveal) base.offscreen=reveal.side+' (scrolled into view before input)';
    // "The input went in but was rejected": a field the page marks invalid, with its message when it has one.
    // :user-invalid only fires after interaction, so an untouched required field is not reported.
    // Native constraints bind only through a validating form: outside one the page keeps the value, and reporting
    // e.g. a step mismatch as a rejection made the outer agent undo a correct 0.0425 (BUG-0064).
    const pageInvalid=e.getAttribute('aria-invalid')==='true';
    let nativeInvalid=false;
    try { nativeInvalid=!!e.form && !e.form.noValidate && e.matches(':user-invalid'); } catch (_) {}
    if (pageInvalid || nativeInvalid) {
      const refs=((e.getAttribute('aria-errormessage')||'')+' '+(e.getAttribute('aria-describedby')||'')).split(/\s+/)
        .map(id=>id && document.getElementById(id)).filter(Boolean).map(n=>n.innerText.trim()).filter(Boolean);
      const message=pageInvalid ? refs.join(' ') || e.validationMessage : e.validationMessage;
      base.invalid=(message || 'invalid').slice(0,200);
    }
    if (reveal) base._distance=reveal.distance;
    const into=reveal ? hidden : actions;
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        into.push({...base,kind:'select',value:o.value,option:o.label,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
    } else {
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (['textbox','searchbox','spinbutton'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      into.push({...base,kind:editable?'fill':'click',value});
      if (editable) into.push({...base,kind:'click',value,label:'Open '+base.label});
    }
  }
  hidden.sort((a,b)=>a._distance-b._distance);
  actions.push(...hidden.slice(0,40).map(({_distance,...a})=>a));
  // ADR-0046: drag sources and drop areas, read only when the run opted into drag (window.__jevDrag) because the
  // cursor scan touches every element. They stay out of `actions`, so the marker and guards of ordinary runs are unchanged.
  const drag_sources=[], drop_targets=[];
  if (window.__jevDrag) {
    const inView = r => r.width>=8 && r.height>=8 && r.x+r.width/2>=0 && r.x+r.width/2<innerWidth &&
      r.y+r.height/2>=0 && r.y+r.height/2<innerHeight;
    const brief = (e,fallback) => (e.getAttribute('aria-label') || e.getAttribute('title') ||
      (e.innerText||'').trim().split('\n')[0] || fallback || '').trim().slice(0,80);
    const grab = /^(grab|grabbing|move|all-scroll)$/;
    // Libraries that turn a plain element into a drag source mark it by class or data attribute rather than draggable/cursor.
    const dragMark = '[aria-roledescription="sortable"],[aria-roledescription="draggable"],[data-rbd-draggable-id],'+
      '[data-rfd-draggable-id],[data-draggable],[data-sortable-id],[class~="ui-draggable"],[class~="draggable"],'+
      '[class~="sortable-item"],[class~="sortable-handle"],[class~="ui-sortable-handle"],[class~="handle"]';
    const draggableNow = e => e.getAttribute('draggable')==='true' || e.hasAttribute('aria-grabbed') || e.matches(dragMark) ||
      (grab.test(getComputedStyle(e).cursor) && (!e.parentElement || getComputedStyle(e.parentElement).cursor!==getComputedStyle(e).cursor));
    for (const e of all) {
      if (drag_sources.length>=40) break;
      if (e===document.body || e===document.documentElement || !visible(e) || !draggableNow(e)) continue;
      const r=e.getBoundingClientRect();
      if (!inView(r)) continue;
      drag_sources.push({node:identity(e),label:brief(e,e.id||e.tagName.toLowerCase()),rect:{x:r.x,y:r.y,w:r.width,h:r.height}});
    }
    const zoneSelector='[aria-dropeffect],[dropzone],[data-droppable],[data-dropzone],[data-drop-zone],[data-rbd-droppable-id],'+
      '[class*="drop" i],[id*="drop" i],[class*="zone" i],[id*="zone" i],[class*="lane" i],[class*="bucket" i],'+
      '[class*="slot" i],[class*="basket" i],[class*="trash" i],[class*="target" i],[id*="target" i]';
    const zones=[...document.querySelectorAll(zoneSelector)].filter(e=>visible(e) && inView(e.getBoundingClientRect()));
    for (const e of zones) {
      if (drop_targets.length>=40) break;
      // A wrapper around another match is the same area seen twice; the innermost is the one to release over.
      if (zones.some(o=>o!==e && e.contains(o))) continue;
      const r=e.getBoundingClientRect();
      // The area's text is often its first card; the suffix keeps it apart from that card as an end.
      drop_targets.push({node:identity(e),label:brief(e,e.id||e.className?.toString?.().split(/\s+/)[0]||'drop area')+' (drop area)',
        rect:{x:r.x,y:r.y,w:r.width,h:r.height}});
    }
  }
  const words=[], walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  const range=document.createRange(); let node,length=0;
  while ((node=walker.nextNode()) && length<6000) {
    const value=node.textContent.trim(), parent=node.parentElement;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node); const r=range.getBoundingClientRect();
    if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
      words.push(value); length+=value.length;
    }
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  // E2E-I29: an open dialog is usually appended last, so its question fell outside the start of the page text.
  const dialogs=[...document.querySelectorAll('[role="dialog"],[role="alertdialog"],[aria-modal="true"],dialog[open]')]
    .filter(d=>visible(d) && d.getBoundingClientRect().width>0)
    .map(d=>d.innerText.trim().replace(/\n{2,}/g,'\n').slice(0,1500)).filter(Boolean).slice(0,3);
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guardNode(a.node);
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6],unsupported];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  // A nested scrollable container (e.g. an app shell that keeps the outer document fixed at one
  // viewport and scrolls its own content pane) clips candidates that document-level scroll_down/up
  // can never reveal, because those only look at document.documentElement.scrollHeight above. Offer
  // a remedy scoped to each such container instead of silently dropping the candidates it clips.
  for (const [cid,container] of clippingContainers) {
    const style=getComputedStyle(container);
    if (!/(auto|scroll)/.test(style.overflowY) || container.scrollHeight<=container.clientHeight+1) continue;
    if (container.scrollTop+container.clientHeight<container.scrollHeight-2)
      actions.push({id:'scroll_down_'+cid,kind:'scroll',label:'Scroll down within this section',delta:560,node:cid});
    if (container.scrollTop>0)
      actions.push({id:'scroll_up_'+cid,kind:'scroll',label:'Scroll up within this section',delta:-560,node:cid});
  }
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions,unsupported,dialogs,
    ...(window.__jevDrag ? {drag_sources,drop_targets} : {})};
})()
