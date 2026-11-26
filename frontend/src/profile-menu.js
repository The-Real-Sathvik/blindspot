import React, { useEffect, useRef, useState } from 'react';

const h = React.createElement;

export function ProfileMenuContent({ onChoose }) {
  return h('div', {className:'profile-popover', role:'menu', 'aria-label':'Local workspace menu'},
    h('div', {className:'profile-caption'}, 'LOCAL WORKSPACE', h('small', null, 'Single-user, no sign-in')),
    h('button', {type:'button', role:'menuitem', onClick:()=>onChoose('profile')}, 'Profile'),
    h('button', {type:'button', role:'menuitem', onClick:()=>onChoose('settings')}, 'Settings'),
    h('div', {className:'menu-divider'}),
    h('button', {type:'button', role:'menuitem', disabled:true, 'aria-describedby':'logout-explanation'}, 'Log out'),
    h('p', {id:'logout-explanation'}, 'Unavailable: this local app has no signed-in session.'));
}

export function ProfileMenu({ onChoose }) {
  const [open, setOpen] = useState(false);
  const container = useRef(null);
  const trigger = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const first = container.current?.querySelector('[role="menuitem"]:not(:disabled)');
    first?.focus();
    function key(event) {
      if (event.key === 'Escape') { event.preventDefault(); setOpen(false); trigger.current?.focus(); return; }
      if (event.key === 'Tab') { setOpen(false); return; }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      const items = Array.from(container.current?.querySelectorAll('[role="menuitem"]:not(:disabled)') || []);
      if (!items.length) return;
      event.preventDefault();
      const index = items.indexOf(document.activeElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : event.key === 'ArrowDown' ? (index + 1) % items.length : (index - 1 + items.length) % items.length;
      items[next].focus();
    }
    function outside(event) { if (!container.current?.contains(event.target)) setOpen(false); }
    document.addEventListener('keydown', key); document.addEventListener('pointerdown', outside);
    return () => { document.removeEventListener('keydown', key); document.removeEventListener('pointerdown', outside); };
  }, [open]);
  return h('div', {className:'profile-menu', ref:container},
    h('button', {ref:trigger, type:'button', className:'avatar profile-trigger', 'aria-label':'Open local workspace menu', 'aria-haspopup':'menu', 'aria-expanded':open, onClick:()=>setOpen(!open)}, 'R'),
    open && h(ProfileMenuContent, {onChoose:(item)=>{ setOpen(false); onChoose(item); trigger.current?.focus(); }}));
}
