import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Anchor, Ellipsis, X } from 'lucide-react';

export function AnchorTile() {
  return <span className="anchor-tile" aria-hidden="true"><Anchor size={17} /></span>;
}

export function IconButton({ label, className, children, ...props }:
  ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return <button type="button" aria-label={label} title={label} className={`icon-button ${className ?? ''}`} {...props}>
    {children}
  </button>;
}

export function Popup({ label, compact, children }: {
  label: string;
  compact?: boolean;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const id = useId();

  useLayoutEffect(() => {
    const popup = content.current;
    const button = trigger.current;
    if (!open || !popup || !button) return;
    function position() {
      if (!popup || !button) return;
      const bounds = button.getBoundingClientRect();
      const left = Math.max(8, Math.min(bounds.right - popup.offsetWidth, window.innerWidth - popup.offsetWidth - 8));
      const above = bounds.bottom + popup.offsetHeight + 8 > window.innerHeight && bounds.top > window.innerHeight / 2;
      const top = above ? bounds.top - popup.offsetHeight - 4 : bounds.bottom + 4;
      popup.style.left = `${left}px`;
      popup.style.top = `${Math.max(8, top)}px`;
    }
    popup.showPopover();
    position();
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => {
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
      if (popup.matches(':popover-open')) popup.hidePopover();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      trigger.current?.focus();
      setOpen(false);
    }
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  return <div className={`popup ${compact ? 'popup-compact' : ''}`} ref={root} onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }}>
    <button type="button" className="icon-button" ref={trigger} aria-label={label} title={label}
      aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(!open)}>
      <Ellipsis size={18} />
    </button>
    {open && <div className="popup-content" id={id} ref={content} popover="manual">{children(() => {
      trigger.current?.focus();
      setOpen(false);
    })}</div>}
  </div>;
}

export function Dialog({ title, wide, onClose, children }: {
  title: string;
  wide?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useLayoutEffect(() => {
    if (!ref.current?.open) ref.current?.showModal();
  }, []);

  return <dialog ref={ref} aria-labelledby={titleId} className={wide ? 'dialog-wide' : undefined}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="dialog-panel">
      <header className="dialog-header">
        <h2 id={titleId}>{title}</h2>
        <IconButton label="닫기" onClick={onClose}><X size={19} /></IconButton>
      </header>
      {children}
    </div>
  </dialog>;
}
