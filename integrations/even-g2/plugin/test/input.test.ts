import { OsEventTypeList } from '@evenrealities/even_hub_sdk'
import { describe, expect, it } from 'vitest'

import { toInput } from '../src/input'

describe('gesture mapping', () => {
  it('a list tap carries the selected index, defaulting to 0', () => {
    expect(toInput({ listEvent: { currentSelectItemIndex: 2 } as any })).toEqual({ type: 'click', index: 2 })
    expect(toInput({ listEvent: {} as any })).toEqual({ type: 'click', index: 0 })
  })

  it('text and system envelopes map swipes, double and long press', () => {
    expect(toInput({ textEvent: { eventType: OsEventTypeList.SCROLL_TOP_EVENT } as any })).toEqual({ type: 'up' })
    expect(toInput({ textEvent: { eventType: OsEventTypeList.SCROLL_BOTTOM_EVENT } as any })).toEqual({ type: 'down' })
    expect(toInput({ sysEvent: { eventType: OsEventTypeList.DOUBLE_CLICK_EVENT } as any })).toEqual({ type: 'double' })
    expect(toInput({ sysEvent: { eventType: OsEventTypeList.LONG_PRESS_EVENT } as any })).toEqual({ type: 'long' })
    expect(toInput({ sysEvent: {} as any })).toEqual({ type: 'click' })
  })

  it('ignores release, lifecycle and audio-only envelopes; exit stops the app', () => {
    expect(toInput({ sysEvent: { eventType: OsEventTypeList.LONG_PRESS_RELEASE_EVENT } as any })).toBeNull()
    expect(toInput({ sysEvent: { eventType: OsEventTypeList.FOREGROUND_ENTER_EVENT } as any })).toBeNull()
    expect(toInput({ audioEvent: {} as any })).toBeNull()
    expect(toInput({ sysEvent: { eventType: OsEventTypeList.SYSTEM_EXIT_EVENT } as any })).toBe('exit')
  })
})
