import { QRCodeSVG } from "qrcode.react";

// Deliberately fixed black-on-white, ignoring the app's dark theme entirely.
// QR scanners rely on standard high contrast — an orange-on-black or
// dark-on-dark code can genuinely fail to scan on a real camera, which would
// defeat the point of offering one at all.
export default function DropQRCode({ value }: { value: string }) {
  return (
    <div className="qr-box">
      <QRCodeSVG value={value} size={180} bgColor="#ffffff" fgColor="#000000" includeMargin />
    </div>
  );
}
