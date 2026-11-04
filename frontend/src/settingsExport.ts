export async function downloadData(format: 'json' | 'txt'): Promise<void> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 120000)
  try {
    const response = await fetch(`/api/export.${format}`, { signal: controller.signal })
    if (!response.ok) throw new Error('导出未完成，请检查本地服务后重试。')
    const expected = format === 'json' ? 'application/json' : 'text/plain'
    if (!response.headers.get('Content-Type')?.startsWith(expected))
      throw new Error('导出文件格式不正确，请刷新后重试。')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = format === 'json' ? 'campusinbox-data.json' : 'campusinbox-report.txt'
    document.body.append(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 60000)
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError')
      throw new Error('导出超时，请重试。')
    if (error instanceof TypeError) throw new Error('无法连接本地服务，请确认后端已启动。')
    throw error
  } finally {
    window.clearTimeout(timer)
  }
}
