import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

/* ----- Button ------------------------------------------------------------ */
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost";
  size?: "md" | "sm";
  block?: boolean;
  loading?: boolean;
};
export function Button({
  variant = "primary",
  size = "md",
  block,
  loading,
  children,
  className = "",
  disabled,
  ...rest
}: ButtonProps) {
  const cls = [
    "btn",
    variant === "primary" ? "btn-primary" : "btn-ghost",
    size === "sm" ? "btn-sm" : "",
    block ? "btn-block" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button className={cls} disabled={disabled || loading} {...rest}>
      {loading && <span className="spinner" aria-hidden />}
      {children}
    </button>
  );
}

/* ----- Field / inputs ---------------------------------------------------- */
export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label className="label">{label}</label>
      {children}
      {hint && !error && <span className="hint">{hint}</span>}
      {error && <span className="field-error">{error}</span>}
    </div>
  );
}

export function TextInput({
  invalid,
  className = "",
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }) {
  return (
    <input
      className={`input ${invalid ? "input-invalid" : ""} ${className}`}
      {...rest}
    />
  );
}

export function Select({
  invalid,
  className = "",
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { invalid?: boolean }) {
  return (
    <select
      className={`select ${invalid ? "input-invalid" : ""} ${className}`}
      {...rest}
    >
      {children}
    </select>
  );
}

export function Textarea({
  className = "",
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`textarea ${className}`} {...rest} />;
}

/* ----- Toggle ------------------------------------------------------------ */
export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="toggle"
      onClick={() => !disabled && onChange(!checked)}
      aria-pressed={checked}
      disabled={disabled}
      style={disabled ? { opacity: 0.6, cursor: "not-allowed" } : undefined}
    >
      <span className={`toggle-track ${checked ? "on" : ""}`}>
        <span className="toggle-knob" />
      </span>
      {label && <span>{label}</span>}
    </button>
  );
}

/* ----- Badge ------------------------------------------------------------- */
export function Badge({
  tone = "gray",
  children,
}: {
  tone?: "green" | "pink" | "gray" | "amber";
  children: ReactNode;
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

/* ----- Banner ------------------------------------------------------------ */
export function Banner({
  tone = "info",
  icon,
  children,
}: {
  tone?: "info" | "warn" | "neutral";
  icon?: string;
  children: ReactNode;
}) {
  const fallback = tone === "warn" ? "!" : "i";
  return (
    <div className={`banner banner-${tone}`}>
      <span className="banner-icon">{icon ?? fallback}</span>
      <div>{children}</div>
    </div>
  );
}

/* ----- States ------------------------------------------------------------ */
export function Spinner() {
  return <span className="spinner" aria-label="carregando" />;
}

export function Loading({ label = "Carregando…" }: { label?: string }) {
  return (
    <div className="loading-row">
      <Spinner /> {label}
    </div>
  );
}

export function EmptyState({
  title,
  message,
  action,
}: {
  title: string;
  message?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {message && <p>{message}</p>}
      {action && <div style={{ marginTop: 16 }}>{action}</div>}
    </div>
  );
}

/* ----- Modal ------------------------------------------------------------- */
export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="modal-overlay" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Fechar">
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}
