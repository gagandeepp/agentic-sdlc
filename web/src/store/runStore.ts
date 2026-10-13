import { create } from 'zustand'
import type { RunEvent } from '../types/api'
import { initialRunState, reduceEvents, type RunViewState } from './reducer'

export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'ended' | 'not_found'

interface RunStore {
  state: RunViewState
  connection: ConnectionState
  reset: (runId: string) => void
  apply: (events: RunEvent[]) => void
  setConnection: (connection: ConnectionState) => void
}

export const useRunStore = create<RunStore>()((set) => ({
  state: initialRunState(),
  connection: 'connecting',
  reset: (runId) => set({ state: initialRunState(runId), connection: 'connecting' }),
  apply: (events) => set((s) => ({ state: reduceEvents(s.state, events) })),
  setConnection: (connection) => set({ connection }),
}))
