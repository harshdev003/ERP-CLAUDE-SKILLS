//
//  set_output.swift
//  Prints or switches the Mac's default audio output device.
//
//  Web voice samples play through the default output. Pointing it at BlackHole for the length
//  of a capture records the sample digitally, with no download and no room noise; the caller
//  switches it back afterwards (`get` before, `set` the printed name after).
//
//  Usage: swift set_output.swift get
//         swift set_output.swift set "<device name>"
//

import CoreAudio
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8) ?? Data())
    exit(1)
}

func address(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                               mElement: kAudioObjectPropertyElementMain)
}

func name(of id: AudioDeviceID) -> String? {
    var addr = address(kAudioObjectPropertyName)
    var cfName: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &cfName) == noErr else { return nil }
    return cfName?.takeRetainedValue() as String?
}

func devices() -> [AudioDeviceID] {
    var addr = address(kAudioHardwarePropertyDevices)
    var size: UInt32 = 0
    let system = AudioObjectID(kAudioObjectSystemObject)
    guard AudioObjectGetPropertyDataSize(system, &addr, 0, nil, &size) == noErr else { return [] }
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(system, &addr, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

let args = CommandLine.arguments
var outputAddress = address(kAudioHardwarePropertyDefaultOutputDevice)
let system = AudioObjectID(kAudioObjectSystemObject)

switch (args.count, args.count > 1 ? args[1] : "") {
case (2, "get"):
    var id = AudioDeviceID(0)
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(system, &outputAddress, 0, nil, &size, &id) == noErr,
          let current = name(of: id) else { fail("cannot read the default output") }
    print(current)
case (3, "set"):
    guard var id = devices().first(where: { name(of: $0) == args[2] }) else { fail("no device named \"\(args[2])\"") }
    let status = AudioObjectSetPropertyData(system, &outputAddress, 0, nil,
                                            UInt32(MemoryLayout<AudioDeviceID>.size), &id)
    guard status == noErr else { fail("could not switch output (status \(status))") }
    print("output → \(args[2])")
default:
    fail("usage: swift set_output.swift get | set \"<device name>\"")
}
