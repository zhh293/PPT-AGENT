import { useRef, useEffect } from 'react'

const EVENT_COLORS = {
  phase_started: 'text-blue-400',
  phase_completed: 'text-green-400',
  artifact_written: 'text-purple-400',
  tool_call_start: 'text-yellow-400',
  tool_call_result: 'text-yellow-300',
  llm_call_start: 'text-cyan-400',
  llm_call_result: 'text-cyan-300',
  warning: 'text-amber-400',
  error: 'text-red-400',
  progress: 'text-gray-400',
  agent_spawn: 'text-indigo-400',
  agent_complete: 'text-indigo-300',
  skill_loaded: 'text-pink-400',
}

function formatTime(ts) {
  if (!ts) return ''
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    return ''
  }
}

export default function EventStream({ events }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  if (events.length === 0) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 text-center text-gray-600">
        暂无事件，启动任务后会实时显示
      </div>
    )
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div className="p-3 border-b border-gray-800 flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500">实时事件流</span>
        <span className="text-xs text-gray-600">{events.length} 条事件</span>
      </div>
      <div className="max-h-[500px] overflow-y-auto p-3 space-y-1 font-mono text-xs">
        {events.map((evt, i) => (
          <div key={i} className="event-item flex gap-2 py-0.5">
            <span className="text-gray-600 shrink-0 w-16">
              {formatTime(evt.timestamp)}
            </span>
            <span className={`shrink-0 w-32 ${EVENT_COLORS[evt.type] || 'text-gray-500'}`}>
              {evt.type}
            </span>
            {evt.phase && (
              <span className="text-gray-500 shrink-0 w-36 truncate">
                [{evt.phase}]
              </span>
            )}
            <span className="text-gray-300 truncate">
              {evt.message}
            </span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
