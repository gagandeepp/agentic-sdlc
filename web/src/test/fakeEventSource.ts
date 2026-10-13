export class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static instances: FakeEventSource[] = []
  onopen: ((event: Event) => void) | null = null
  onmessage: ((message: MessageEvent<string>) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  readyState = FakeEventSource.CONNECTING
  closed = false

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this)
  }

  close(): void {
    this.closed = true
    this.readyState = FakeEventSource.CLOSED
  }

  emit(data: unknown): void {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }))
  }

  /** The browser gave up: a non-200 / non-event-stream reply ends EventSource for good. */
  fail(): void {
    this.readyState = FakeEventSource.CLOSED
    this.onerror?.(new Event('error'))
  }

  static latest(): FakeEventSource {
    const source = FakeEventSource.instances.at(-1)
    if (!source) throw new Error('no EventSource was opened')
    return source
  }
}
