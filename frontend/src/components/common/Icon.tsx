// Small inline icon set (stroke icons, no icon font or CDN).
type Name =
  | "alert" | "warn" | "check" | "x" | "info" | "search" | "bolt" | "trend" | "clock" | "doc" | "play"
  | "menu" | "refresh" | "flask" | "shield" | "arrow" | "dot" | "upload" | "list";

const PATHS: Record<Name, string> = {
  alert: "M12 3 2 20h20L12 3zm0 6v5m0 3v.01",
  warn: "M12 8v5m0 3v.01M4.9 4.9a10 10 0 1 0 14.2 14.2A10 10 0 0 0 4.9 4.9z",
  check: "M5 12.5 10 17 19 7",
  x: "M6 6l12 12M18 6 6 18",
  info: "M12 11v6m0-9v.01M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zm9 16-4.3-4.3",
  bolt: "M13 3 4 14h7l-1 7 9-11h-7l1-7z",
  trend: "M3 17 9 11l4 4 8-8m0 0h-5m5 0v5",
  clock: "M12 7v5l3 2m6-2a9 9 0 1 1-18 0 9 9 0 0 1 18 0z",
  doc: "M7 3h7l5 5v13H7zM14 3v5h5M10 13h6m-6 4h6",
  play: "M7 5v14l12-7z",
  menu: "M4 7h16M4 12h16M4 17h16",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6",
  flask: "M9 3h6M10 3v6L4.5 18.5A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-2.5L14 9V3",
  shield: "M12 3 4 6v6c0 5 3.4 8.3 8 9 4.6-.7 8-4 8-9V6l-8-3z",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  dot: "M12 12h.01",
  upload: "M12 16V4m0 0-5 5m5-5 5 5M4 20h16",
  list: "M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01",
};

export function Icon({ name, label }: { name: Name; label?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <path d={PATHS[name]} />
    </svg>
  );
}
