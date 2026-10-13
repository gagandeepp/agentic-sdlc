import { create } from 'zustand'
import type { RunEvent } from '../types/api'
import { initialRunState, reduceEvents, type RunViewState } from './reducer'

interface RunStore {
  state: RunViewState
  reset: (runId: string) => void
  apply: (events: RunEvent[]) => void
}

export const useRunStore = create<RunStore>()((set) => ({
  state: initialRunState(),
  reset: (runId) => set({ state: initialRunState(runId) }),
  apply: (events) => set((s) => ({ state: reduceEvents(s.state, events) })),
}))
