const PHASES = [
  { id: 'document_analysis', label: '文档分析', icon: '📄' },
  { id: 'outline_generation', label: '大纲生成', icon: '📋' },
  { id: 'template_matching', label: '模板匹配', icon: '🎨' },
  { id: 'design_planning', label: '设计规划', icon: '✏️' },
  { id: 'content_mapping', label: '内容映射', icon: '📝' },
  { id: 'visual_generation', label: '图片生成', icon: '🖼️' },
  { id: 'ppt_assembly', label: 'PPT 组装', icon: '📊' },
  { id: 'verification', label: '验证检查', icon: '✅' },
]

export default function PhaseProgress({ phases, currentPhase }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
      <h3 className="text-sm font-medium text-gray-400 mb-4">流水线进度</h3>
      <div className="grid grid-cols-4 gap-3">
        {PHASES.map((phase, idx) => {
          const completed = phases.includes(phase.id)
          const active = phase.id === currentPhase
          const pending = !completed && !active

          return (
            <div
              key={phase.id}
              className={`relative rounded-lg p-3 border transition-all ${
                completed
                  ? 'bg-green-950/30 border-green-800'
                  : active
                  ? 'bg-blue-950/30 border-blue-700 ring-1 ring-blue-500/30'
                  : 'bg-gray-900 border-gray-800'
              }`}
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-base">{phase.icon}</span>
                <span className={`text-xs font-medium ${
                  completed ? 'text-green-400' : active ? 'text-blue-400' : 'text-gray-600'
                }`}>
                  {completed ? '完成' : active ? '进行中' : '等待'}
                </span>
              </div>
              <div className={`text-sm font-medium ${
                pending ? 'text-gray-600' : 'text-gray-200'
              }`}>
                {phase.label}
              </div>
              {active && (
                <div className="absolute top-2 right-2">
                  <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
