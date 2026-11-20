import AppKit
let args = CommandLine.arguments
let name = args[1], pts = Double(args[2])!, weightName = args[3], out = args[4], scale = Double(args[5])!
let weights: [String: NSFont.Weight] = ["ultralight": .ultraLight, "thin": .thin, "light": .light, "regular": .regular, "medium": .medium, "semibold": .semibold, "bold": .bold, "heavy": .heavy, "black": .black]
let cfg = NSImage.SymbolConfiguration(pointSize: pts * scale, weight: weights[weightName] ?? .regular)
guard let base = NSImage(systemSymbolName: name, accessibilityDescription: nil), let img = base.withSymbolConfiguration(cfg) else { fputs("no symbol \(name)\n", stderr); exit(2) }
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(img.size.width.rounded(.up)), pixelsHigh: Int(img.size.height.rounded(.up)), bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSColor.black.set(); img.draw(in: NSRect(origin: .zero, size: img.size))
NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
print("\(Int(img.size.width))x\(Int(img.size.height))")
