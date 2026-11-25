// Draws app views on the G2 through the Even Hub SDK. Two layouts:
//   list: a two-line header text + a selectable list (home, choices)
//   text: a paged body + a one-line footer (card, activity, confirm, result)
// A view in the same layout with the same list items only updates text in
// place (no flicker, list selection kept); anything else rebuilds the page.

import {
  CreateStartUpPageContainer,
  ListContainerProperty,
  ListItemContainerProperty,
  RebuildPageContainer,
  TextContainerProperty,
  TextContainerUpgrade,
  type EvenAppBridge,
} from '@evenrealities/even_hub_sdk'

import { BODY_H, HEAD_H, PAD, type Display, type View } from './app'
import { SCREEN_H, SCREEN_W } from './text'

const text = (id: number, name: string, y: number, h: number, content: string, capture: boolean) =>
  new TextContainerProperty({
    xPosition: 0,
    yPosition: y,
    width: SCREEN_W,
    height: h,
    borderWidth: 0,
    borderColor: 0,
    paddingLength: PAD,
    containerID: id,
    containerName: name,
    content: content || ' ',
    isEventCapture: capture ? 1 : 0,
  })

function page(view: View) {
  if (view.layout === 'list') {
    const list = new ListContainerProperty({
      xPosition: 0,
      yPosition: HEAD_H,
      width: SCREEN_W,
      height: SCREEN_H - HEAD_H,
      borderWidth: 0,
      borderColor: 0,
      paddingLength: PAD,
      containerID: 2,
      containerName: 'items',
      isEventCapture: 1,
      itemContainer: new ListItemContainerProperty({
        itemCount: view.items.length,
        itemWidth: SCREEN_W - 2 * PAD,
        isItemSelectBorderEn: 1,
        itemName: view.items,
      }),
    })
    return { containerTotalNum: 2, textObject: [text(1, 'head', 0, HEAD_H, view.head, false)], listObject: [list] }
  }
  return {
    containerTotalNum: 2,
    textObject: [
      text(1, 'body', 0, BODY_H, view.body, true),
      text(2, 'footer', BODY_H, SCREEN_H - BODY_H, view.footer, false),
    ],
  }
}

const sameItems = (a: View, b: View) =>
  a.layout === 'list' && b.layout === 'list' && a.items.length === b.items.length && a.items.every((x, i) => x === b.items[i])

export class GlassesDisplay implements Display {
  private shown: View | null = null
  private created = false
  private queue: Promise<unknown> = Promise.resolve()
  available = true

  constructor(private bridge: EvenAppBridge) {}

  render(view: View): Promise<void> {
    // Serialize: a fast double gesture must not interleave two page writes.
    const run = this.queue.then(() => this.draw(view)).catch(err => {
      this.available = false
      console.error('glasses render failed', err)
    })
    this.queue = run
    return run
  }

  private async draw(view: View) {
    const prev = this.shown
    if (prev && prev.layout === view.layout && (view.layout === 'text' || sameItems(prev, view))) {
      if (view.layout === 'list' && prev.layout === 'list' && prev.head !== view.head) {
        await this.upgrade(1, 'head', view.head)
      }
      if (view.layout === 'text' && prev.layout === 'text') {
        if (prev.body !== view.body) await this.upgrade(1, 'body', view.body)
        if (prev.footer !== view.footer) await this.upgrade(2, 'footer', view.footer)
      }
      this.shown = view
      return
    }
    const layout = page(view)
    if (!this.created) {
      const result = await this.bridge.createStartUpPageContainer(new CreateStartUpPageContainer(layout))
      if (result !== 0) throw new Error(`createStartUpPageContainer returned ${result}`)
      this.created = true
    } else {
      await this.bridge.rebuildPageContainer(new RebuildPageContainer(layout))
    }
    this.shown = view
  }

  private upgrade(id: number, name: string, content: string) {
    return this.bridge.textContainerUpgrade(
      new TextContainerUpgrade({ containerID: id, containerName: name, content: content || ' ' }),
    )
  }

  async exit() {
    await this.bridge.shutDownPageContainer(1)
  }
}
