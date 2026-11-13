### Dense Compute link ends

- The routed canvas collapses more than three incident link ends into one device badge, keeping the existing worst-known-state order, shape and a count. The badge sits outside the device disc, with an 11px count beside the mark, clear of every link stroke and label, so a narrow corridor cannot turn its end marks into an overlapping chain.

- Selection reveals only the selected link's original end marks and removes it from the remaining badge's count and state. Stroke geometry and individual pointer and keyboard targets stay intact. Sparse devices, unclaimed ends and the classic canvas keep their existing rendering.

- The workspace map wrapper restores the existing 16px phone inset around its tabs and nested map content. The regression was already present before the aggregate-count change. Management retains the same inset. Desktop rules and classic styling are unchanged.
