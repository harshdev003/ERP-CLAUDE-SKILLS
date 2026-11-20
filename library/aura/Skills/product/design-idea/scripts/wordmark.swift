import AppKit
// Rasterizes a template PDF asset (the app's `aurieLogo.imageset/aurieLogo.pdf`) to a
// PNG at a given pixel height; the renderer keeps only its alpha and tints it per sky.
let args = CommandLine.arguments
let src = args[1], dst = args[2], height = CGFloat(Double(args[3])!)
guard let img = NSImage(contentsOfFile: src) else { fputs("no image at \(src)\n", stderr); exit(2) }
let scale = height / img.size.height
let size = NSSize(width: (img.size.width * scale).rounded(), height: height)
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width), pixelsHigh: Int(size.height), bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSGraphicsContext.current?.imageInterpolation = .high
img.draw(in: NSRect(origin: .zero, size: size))
NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: dst))
print("\(Int(size.width))x\(Int(size.height))")
