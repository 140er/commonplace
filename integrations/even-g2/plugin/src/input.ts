// Map an Even Hub event envelope to one app gesture.
//
// Clicks are protobuf zero values, so a tap arrives with `eventType` undefined:
// default to CLICK only inside an envelope that is actually present. Lists
// report clicks through listEvent (with the selected index), text containers
// through textEvent or sysEvent depending on firmware; take the most specific.

import { OsEventTypeList, type EvenHubEvent } from '@evenrealities/even_hub_sdk'

import type { Input } from './app'

export function toInput(event: EvenHubEvent): Input | 'exit' | 'resume' | null {
  const envelope = event.listEvent ?? event.textEvent ?? event.sysEvent
  if (!envelope) return null
  const type = envelope.eventType ?? OsEventTypeList.CLICK_EVENT
  switch (type) {
    case OsEventTypeList.CLICK_EVENT:
      return event.listEvent
        ? {
            type: 'click',
            index: event.listEvent.currentSelectItemIndex ?? 0,
            ...(event.listEvent.currentSelectItemName ? { name: event.listEvent.currentSelectItemName } : {}),
          }
        : { type: 'click' }
    case OsEventTypeList.DOUBLE_CLICK_EVENT:
      return { type: 'double' }
    case OsEventTypeList.SCROLL_TOP_EVENT:
      return { type: 'up' }
    case OsEventTypeList.SCROLL_BOTTOM_EVENT:
      return { type: 'down' }
    case OsEventTypeList.LONG_PRESS_EVENT:
      return { type: 'long' }
    case OsEventTypeList.FOREGROUND_ENTER_EVENT:
      return 'resume'
    case OsEventTypeList.SYSTEM_EXIT_EVENT:
    case OsEventTypeList.ABNORMAL_EXIT_EVENT:
      return 'exit'
    default:
      return null
  }
}
