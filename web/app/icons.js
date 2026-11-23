const shapes = {
  plug: <><path d="M9 3v5m6-5v5M7 8h10v2a5 5 0 0 1-5 5v5M7 8v2a5 5 0 0 0 5 5"/></>,
  bag: <><path d="M5 8h14l1 13H4L5 8Z"/><path d="M9 10V6a3 3 0 0 1 6 0v4"/></>,
  chat: <path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l1.6-3.1A7.5 7.5 0 1 1 20 11.5Z"/>,
  search: <><circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 4 4"/></>,
  charger: <path d="M13 2 5 13h6l-1 9 9-12h-6l1-8Z"/>,
  cable: <><path d="M8 3v6a4 4 0 0 0 8 0V3M5 3h6m2 0h6M12 13v4a4 4 0 0 0 8 0v-2"/></>,
  battery: <><rect x="4" y="7" width="15" height="10" rx="2"/><path d="M22 10v4m-14-2h6"/></>,
  hub: <><rect x="3" y="8" width="18" height="10" rx="2"/><path d="M7 13h2m3 0h2m3 0h1M12 8V4"/></>,
  audio: <><path d="M5 14v-2a7 7 0 0 1 14 0v2"/><rect x="4" y="13" width="4" height="6" rx="2"/><rect x="16" y="13" width="4" height="6" rx="2"/></>,
};

export default function Icon({ name, size = 22 }) {
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">{shapes[name]}</svg>;
}
