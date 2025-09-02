import React, { useEffect } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

export default function MapView({ shipment }) {
  useEffect(() => {
    if (!shipment) return
    const map = L.map('map').setView([0, 0], 2)
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map)
    const [lat, lng] = shipment.origin.split(',').map(Number)
    L.marker([lat, lng]).addTo(map).bindPopup(shipment.title)
  }, [shipment])

  return <div id="map" style={{ flex: 1 }} />
}
