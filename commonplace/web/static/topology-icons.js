/* Device icons of the Compute map, drawn for this app: plain strokes in a 40 by 40 box
   centred on 0,0 so a node is one translate. One entry per node kind of docs/topology.md,
   plus "unknown" for a node whose kind is not recorded or has expired. */
export const KINDS = {
  pc: { label: "PC", icon: '<rect x="-18" y="-13" width="24" height="17" rx="1.5"/><path d="M-6 4v5.5M-11 9.5h10"/><rect x="10" y="-13" width="8.5" height="23" rx="1.5"/><path d="M13 -8.5h2.5M13 -5h2.5M14.2 5.5h.1"/>' },
  mac: { label: "Mac", icon: '<rect x="-14" y="-12" width="28" height="18.5" rx="2"/><path d="M-19 10h38l-2.5 3.5h-33zM-3 10.2h6"/>' },
  phone: { label: "Phone", icon: '<rect x="-8.5" y="-17" width="17" height="34" rx="3.5"/><path d="M-2.5 12.5h5M-1.5 -13h3"/>' },
  vm: { label: "VM", icon: '<rect x="-17" y="-17" width="24" height="24" rx="2" stroke-dasharray="3 2.6"/><rect x="-7" y="-7" width="24" height="24" rx="2"/><path d="m0 1.5 4 3.5-4 3.5M7 9.5h5"/>' },
  server: { label: "Server", icon: '<rect x="-15" y="-16.5" width="30" height="9.5" rx="1.5"/><rect x="-15" y="-4.75" width="30" height="9.5" rx="1.5"/><rect x="-15" y="7" width="30" height="9.5" rx="1.5"/><path d="M-10.5 -11.75h.1M-10.5 0h.1M-10.5 11.75h.1M-3 -11.75h13M-3 0h13M-3 11.75h13"/>' },
  router: { label: "Router or edge", icon: '<rect x="-17" y="2" width="34" height="11.5" rx="2"/><path d="M-9.5 2v-14M9.5 2v-14M-11.5 7.75h.1M-6.5 7.75h.1M3.5 7.75h8.5"/><path d="M-13.5 -13.5a5.6 5.6 0 0 1 8 0M5.5 -13.5a5.6 5.6 0 0 1 8 0"/>' },
  cloud: { label: "Cloud or CDN", icon: '<path d="M-11 12h21a8 8 0 0 0 1.5-15.9A11 11 0 0 0-9.6-6.5 9.3 9.3 0 0 0-11 12z"/>' },
  website: { label: "Website", icon: '<rect x="-17" y="-15" width="34" height="30" rx="2.5"/><path d="M-17 -7.5h34M-12.5 -11.25h.1M-8.5 -11.25h.1"/><circle cx="0" cy="3.75" r="6.5"/><path d="M-6.5 3.75h13M0 -2.75c-3.6 3.6-3.6 9.4 0 13M0 -2.75c3.6 3.6 3.6 9.4 0 13"/>' },
  container: { label: "Container or app", icon: '<path d="M0 -17 14.5 -8.5v17L0 17l-14.5-8.5v-17zM-14.5 -8.5 0 0l14.5-8.5M0 0v17"/>' },
  unknown: { label: "Unknown device", icon: '<rect x="-14" y="-14" width="28" height="28" rx="5" stroke-dasharray="3 3"/><path d="M-4.2 -4.5a4.2 4.2 0 1 1 5.9 3.9c-1.1.5-1.7 1.3-1.7 2.8M0 7.6h.1"/>' },
};

export const kindOf = (kind) => (Object.hasOwn(KINDS, kind) ? kind : "unknown");
