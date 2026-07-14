import { useState, useRef } from 'react'

export default function FileUpload({ onClose, onCreated }) {
  const [files, setFiles] = useState([])
  const [prompt, setPrompt] = useState('')
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState(null)
  const inputRef = useRef()

  const handleDrop = (e) => {
    e.preventDefault()
    const dropped = Array.from(e.dataTransfer.files)
    setFiles((prev) => [...prev, ...dropped])
  }

  const handleSubmit = async () => {
    if (files.length === 0) return
    setUploading(true)
    setError(null)

    const formData = new FormData()
    files.forEach((f) => formData.append('files', f))
    if (prompt.trim()) formData.append('user_prompt', prompt.trim())

    try {
      const res = await fetch('/api/jobs', { method: 'POST', body: formData })
      if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
      const job = await res.json()
      onCreated(job)
    } catch (e) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-2xl p-6 w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-white mb-4">新建 PPT 生成任务</h2>

        {/* User prompt */}
        <div className="mb-4">
          <label className="block text-sm text-gray-400 mb-1">生成要求（可选）</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="例如：做成投资路演风格，重点突出技术优势和市场规模，面向C端投资人..."
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 resize-none focus:outline-none focus:border-blue-500 transition-colors"
            rows={3}
          />
        </div>

        {/* Drop zone */}
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          className="border-2 border-dashed border-gray-700 hover:border-blue-500 rounded-xl p-8 text-center cursor-pointer transition-colors"
        >
          <div className="text-gray-400 mb-2">拖拽文件到这里，或点击选择</div>
          <div className="text-xs text-gray-600">支持 .md .txt .docx .pdf 等文件</div>
          <input
            ref={inputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => setFiles((prev) => [...prev, ...Array.from(e.target.files)])}
          />
        </div>

        {/* File list */}
        {files.length > 0 && (
          <div className="mt-4 space-y-2">
            {files.map((f, i) => (
              <div key={i} className="flex items-center justify-between bg-gray-800 rounded-lg px-3 py-2 text-sm">
                <span className="text-gray-300 truncate">{f.name}</span>
                <button
                  onClick={() => setFiles(files.filter((_, j) => j !== i))}
                  className="text-gray-500 hover:text-red-400 ml-2"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}

        {error && <div className="mt-3 text-sm text-red-400">{error}</div>}

        {/* Actions */}
        <div className="mt-6 flex justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors">
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={files.length === 0 || uploading}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm font-medium rounded-lg transition-colors"
          >
            {uploading ? '上传中...' : '创建任务'}
          </button>
        </div>
      </div>
    </div>
  )
}
