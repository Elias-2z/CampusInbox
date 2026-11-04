export function canExtractLocally(
  name: string,
  size: number,
  available: boolean,
  docxAvailable = false,
): boolean {
  return (
    size > 0 &&
    size <= 8 * 1024 * 1024 &&
    ((available && /\.(png|jpe?g|webp|bmp|pdf)$/i.test(name)) ||
      (docxAvailable && /\.docx$/i.test(name)))
  )
}
