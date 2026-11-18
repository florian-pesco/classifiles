import { createContext, useContext } from 'react'

/** Page-wide messages: `notify` shows a short confirmation, `fail` shows an error banner. */
export type Messages = {
  notify: (message: string) => void
  fail: (error: unknown) => void
}

export const MessagesContext = createContext<Messages>({ notify: () => undefined, fail: () => undefined })

export const useMessages = () => useContext(MessagesContext)
