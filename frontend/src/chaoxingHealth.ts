export function coverageLabel(value: string): string {
  return (
    (
      {
        complete: '本轮检查完成',
        loaded_text: '已检查可见文字',
        partial: '部分内容未读取',
        list_only: '仅检查列表',
        unknown: '尚无法确认',
      } as Record<string, string>
    )[value] || '尚无法确认'
  )
}
