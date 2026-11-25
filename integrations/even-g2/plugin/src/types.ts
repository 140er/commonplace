// The slices of Commonplace API responses the glasses use. Shapes follow
// commonplace/boards (card projection, needs-me buckets) and commonplace/events.

export interface Card {
  number: number
  board: string
  title: string
  body?: string | null
  status: string
  ask?: string | null
  ask_options?: string[] | null
  ask_to?: string | null
  return_to?: string | null
  created_by?: string | null
  review_kind?: string | null
  review_context?: string | null
  version: number
  why?: string
}

export interface NeedsMe {
  asks: Card[]
  done_review: Card[]
  backlog_from_agents: Card[]
  ready_to_merge: Card[]
  stale: Card[]
  plans: Card[]
  approvals: Card[]
  count: number
  coordinator_waiting?: unknown[]
  orchestrator_failing?: unknown[]
  orchestrator_stalled?: unknown[]
}

export interface Board {
  slug: string
  name: string
  agents_working?: number
  counts?: Record<string, number>
}

export interface HubEvent {
  seq: number
  at: string
  kind: string
  subject?: string
  actor?: string
  data?: { number?: number; board?: string; title?: string; status?: string; to?: string; from?: string }
}

export interface EventPage {
  events: HubEvent[]
  seq: number
}

export interface Me {
  actor: string
  workspace_name?: string
  people?: Record<string, string>
}
