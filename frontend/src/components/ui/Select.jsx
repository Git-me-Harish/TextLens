/**
 * Select — a styled dropdown that actually matches the rest of the app.
 *
 * Why not just CSS the native <select>? Because the part that looked wrong is
 * the part CSS cannot reach. A native select's closed control is styleable,
 * but the open list is drawn by the operating system — the grey highlight
 * bar, the system font, the square corners. No stylesheet changes that, so
 * the only way to make the open state match the design system is to render
 * the list ourselves.
 *
 * Deliberately keeps the native API shape — `value`, `onChange(event)` with
 * `event.target.value`, and `options: [{value, label}]` — so call sites read
 * the same as the <select> they replace and the swap can't silently change
 * how a handler reads its value.
 *
 * Accessibility is not optional here: replacing a native control means
 * re-implementing what it gave for free.
 *   - button carries role=combobox + aria-expanded + aria-controls
 *   - list is role=listbox, options are role=option with aria-selected
 *   - ArrowUp/Down move the highlight (and open the list when closed)
 *   - Enter/Space select, Escape closes and returns focus to the button
 *   - Home/End jump to first/last
 *   - typing a letter jumps to the next option starting with it
 *   - click outside closes
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronDown, Check } from "lucide-react";

export default function Select({
  value,
  onChange,
  options = [],
  placeholder = "Select…",
  disabled = false,
  minWidth = 150,
  style = {},
  className = "",
  "aria-label": ariaLabel,
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);   // keyboard-highlighted index
  const rootRef = useRef(null);
  const buttonRef = useRef(null);
  const listRef = useRef(null);
  const typeahead = useRef({ term: "", at: 0 });
  const listId = useId();

  const selectedIndex = useMemo(
    () => options.findIndex((o) => String(o.value) === String(value)),
    [options, value]
  );
  const selected = selectedIndex >= 0 ? options[selectedIndex] : null;

  const close = useCallback((refocus = true) => {
    setOpen(false);
    setActive(-1);
    if (refocus) buttonRef.current?.focus();
  }, []);

  const pick = useCallback((option) => {
    // Mimic a native change event so call sites keep using e.target.value.
    onChange?.({ target: { value: option.value } });
    close();
  }, [onChange, close]);

  // Click outside closes. Pointerdown rather than click so it fires before a
  // click lands on something else and reorders the state updates.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e) => {
      if (!rootRef.current?.contains(e.target)) close(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [open, close]);

  // Keep the highlighted option in view when navigating with the keyboard.
  useEffect(() => {
    if (!open || active < 0) return;
    listRef.current?.querySelectorAll("[role=option]")[active]
      ?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  const openList = () => {
    setOpen(true);
    setActive(selectedIndex >= 0 ? selectedIndex : 0);
  };

  const onKeyDown = (e) => {
    if (disabled) return;

    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        openList();
      }
      return;
    }

    switch (e.key) {
      case "Escape":   e.preventDefault(); close(); break;
      case "Tab":      close(false); break;   // let focus move on naturally
      case "ArrowDown":
        e.preventDefault();
        setActive((i) => (i + 1) % options.length);
        break;
      case "ArrowUp":
        e.preventDefault();
        setActive((i) => (i - 1 + options.length) % options.length);
        break;
      case "Home":     e.preventDefault(); setActive(0); break;
      case "End":      e.preventDefault(); setActive(options.length - 1); break;
      case "Enter":
      case " ":
        e.preventDefault();
        if (options[active]) pick(options[active]);
        break;
      default:
        // Type-ahead: printable single characters jump to a matching option.
        if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
          const now = Date.now();
          const t = typeahead.current;
          t.term = now - t.at > 700 ? e.key : t.term + e.key;
          t.at = now;
          const hit = options.findIndex((o) =>
            String(o.label).toLowerCase().startsWith(t.term.toLowerCase())
          );
          if (hit >= 0) setActive(hit);
        }
    }
  };

  return (
    <div
      ref={rootRef}
      className={className}
      style={{ position: "relative", display: "inline-block", minWidth, ...style }}
    >
      <button
        ref={buttonRef}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => (open ? close(false) : openList())}
        onKeyDown={onKeyDown}
        className="select-trigger"
        data-open={open ? "true" : undefined}
      >
        <span className={selected ? "select-value" : "select-placeholder"}>
          {selected ? selected.label : placeholder}
        </span>
        <ChevronDown size={15} className="select-chevron" aria-hidden="true" />
      </button>

      {open && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          tabIndex={-1}
          aria-activedescendant={active >= 0 ? `${listId}-opt-${active}` : undefined}
          className="select-menu"
        >
          {options.map((o, i) => {
            const isSelected = String(o.value) === String(value);
            return (
              <li
                key={`${o.value}`}
                id={`${listId}-opt-${i}`}
                role="option"
                aria-selected={isSelected}
                data-active={i === active ? "true" : undefined}
                data-selected={isSelected ? "true" : undefined}
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(o)}
                className="select-option"
              >
                <span>{o.label}</span>
                {isSelected && <Check size={14} aria-hidden="true" />}
              </li>
            );
          })}
          {options.length === 0 && (
            <li className="select-option select-option--empty">No options</li>
          )}
        </ul>
      )}
    </div>
  );
}
