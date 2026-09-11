/**
 * Scene overlays: build plate, axes, build direction, layer plane, load
 * vectors, fixed surfaces and critical-region markers.
 *
 * The scene is Z-up to match printing convention: the build plate is the XY
 * plane and the build direction is +Z, exactly as in the analysis.
 */

import { Html, Line } from '@react-three/drei'
import { useMemo } from 'react'
import { DoubleSide, Quaternion, Vector3 } from 'three'
import type { CriticalRegion, LoadPathInfo } from '../api/types'

export const COLORS = {
  model: '#8c98a6',
  fixation: '#b06cf5',
  loadFace: '#ff7b45',
  buildDirection: '#4da3ff',
  layerPlane: '#4da3ff',
  info: '#6b7987',
  warning: '#d29922',
  critical: '#f85149',
} as const

export function BuildPlate({ size }: { size: [number, number] }) {
  const [width, depth] = size
  const half: [number, number] = [width / 2, depth / 2]
  const step = 10
  const lines = useMemo(() => {
    const segments: [Vector3, Vector3][] = []
    for (let x = -half[0]; x <= half[0] + 1e-6; x += step) {
      segments.push([new Vector3(x, -half[1], 0), new Vector3(x, half[1], 0)])
    }
    for (let y = -half[1]; y <= half[1] + 1e-6; y += step) {
      segments.push([new Vector3(-half[0], y, 0), new Vector3(half[0], y, 0)])
    }
    return segments
  }, [half[0], half[1]])

  return (
    <group>
      <mesh position={[0, 0, -0.05]} receiveShadow>
        <planeGeometry args={[width, depth]} />
        <meshStandardMaterial color="#11161d" side={DoubleSide} />
      </mesh>
      {lines.map((segment, index) => (
        <Line
          key={index}
          points={segment}
          color="#1d2733"
          lineWidth={1}
          transparent
          opacity={0.9}
        />
      ))}
      <Line
        points={[
          new Vector3(-half[0], -half[1], 0.01),
          new Vector3(half[0], -half[1], 0.01),
          new Vector3(half[0], half[1], 0.01),
          new Vector3(-half[0], half[1], 0.01),
          new Vector3(-half[0], -half[1], 0.01),
        ]}
        color="#2c3a49"
        lineWidth={1.5}
      />
    </group>
  )
}

export function Axes({ length }: { length: number }) {
  const axes: { dir: [number, number, number]; color: string; label: string }[] = [
    { dir: [1, 0, 0], color: '#e05252', label: 'X' },
    { dir: [0, 1, 0], color: '#4caf50', label: 'Y' },
    { dir: [0, 0, 1], color: '#4da3ff', label: 'Z' },
  ]
  return (
    <group>
      {axes.map((axis) => (
        <group key={axis.label}>
          <Line
            points={[
              new Vector3(0, 0, 0),
              new Vector3(axis.dir[0] * length, axis.dir[1] * length, axis.dir[2] * length),
            ]}
            color={axis.color}
            lineWidth={2}
          />
          <Html
            position={[
              axis.dir[0] * length * 1.06,
              axis.dir[1] * length * 1.06,
              axis.dir[2] * length * 1.06,
            ]}
            center
            style={{ color: axis.color, font: '11px ui-monospace, monospace', pointerEvents: 'none' }}
          >
            {axis.label}
          </Html>
        </group>
      ))}
    </group>
  )
}

/** Arrow marking the build direction (+Z) and the stack of layers. */
export function BuildDirection({ height }: { height: number }) {
  const top = height * 1.25 + 10
  return (
    <group>
      <Line
        points={[new Vector3(0, 0, 0), new Vector3(0, 0, top)]}
        color={COLORS.buildDirection}
        lineWidth={2}
        dashed
        dashSize={2}
        gapSize={2}
      />
      <mesh position={[0, 0, top]} rotation={[Math.PI / 2, 0, 0]}>
        <coneGeometry args={[Math.max(1.5, height * 0.03), Math.max(4, height * 0.08), 16]} />
        <meshStandardMaterial color={COLORS.buildDirection} />
      </mesh>
      <Html
        position={[0, 0, top + Math.max(6, height * 0.12)]}
        center
        style={{
          color: COLORS.buildDirection,
          font: '10px ui-monospace, monospace',
          whiteSpace: 'nowrap',
          pointerEvents: 'none',
        }}
      >
        BUILD DIRECTION +Z
      </Html>
    </group>
  )
}

/** A few representative layer planes through the part. */
export function LayerPlanes({ height, span }: { height: number; span: number }) {
  const levels = useMemo(() => {
    const count = 4
    return Array.from({ length: count }, (_, index) => ((index + 1) / (count + 1)) * height)
  }, [height])

  return (
    <group>
      {levels.map((z) => (
        <mesh key={z} position={[0, 0, z]}>
          <planeGeometry args={[span, span]} />
          <meshBasicMaterial
            color={COLORS.layerPlane}
            transparent
            opacity={0.05}
            side={DoubleSide}
            depthWrite={false}
          />
        </mesh>
      ))}
    </group>
  )
}

interface ForceArrowProps {
  path: LoadPathInfo
  scale: number
}

/**
 * Force (or torque) vector drawn at the point the solver actually uses.
 *
 * The arrow length is scaled by magnitude relative to the largest load in the
 * set, so relative magnitudes are readable without the arrow leaving the
 * viewport.
 */
export function ForceArrow({ path, scale }: ForceArrowProps) {
  const force = new Vector3(...path.force_n)
  const torque = new Vector3(...path.torque_nmm)
  const magnitude = force.length()
  const origin = new Vector3(...path.application_point)

  if (magnitude < 1e-9) {
    const axis = torque.clone().normalize()
    const tip = origin.clone().add(axis.clone().multiplyScalar(scale))
    return (
      <group>
        <Line points={[origin, tip]} color={COLORS.loadFace} lineWidth={3} />
        <Html
          position={tip.toArray()}
          center
          style={{
            color: COLORS.loadFace,
            font: '10px ui-monospace, monospace',
            whiteSpace: 'nowrap',
            pointerEvents: 'none',
          }}
        >
          {`${(torque.length() / 1000).toFixed(1)} Nm`}
        </Html>
      </group>
    )
  }

  const direction = force.clone().normalize()
  // Draw the arrow arriving at the application point, like a load in a sketch.
  const tail = origin.clone().sub(direction.clone().multiplyScalar(scale))

  return (
    <group>
      <Line points={[tail, origin]} color={COLORS.loadFace} lineWidth={3} />
      <mesh position={origin.toArray()} quaternion={coneQuaternion(direction)}>
        <coneGeometry args={[scale * 0.08, scale * 0.22, 16]} />
        <meshStandardMaterial color={COLORS.loadFace} />
      </mesh>
      <Html
        position={tail.toArray()}
        center
        style={{
          color: COLORS.loadFace,
          font: '10px ui-monospace, monospace',
          whiteSpace: 'nowrap',
          pointerEvents: 'none',
        }}
      >
        {`${path.name}: ${magnitude.toFixed(0)} N`}
      </Html>
    </group>
  )
}

/** Marker at the reaction point of a load path. */
export function FixtureMarker({ position }: { position: [number, number, number] }) {
  return (
    <mesh position={position}>
      <sphereGeometry args={[1.5, 16, 16]} />
      <meshStandardMaterial color={COLORS.fixation} />
    </mesh>
  )
}

export function RegionMarker({
  region,
  active,
  radius,
  onSelect,
}: {
  region: CriticalRegion
  active: boolean
  radius: number
  onSelect: () => void
}) {
  if (!region.position) return null
  const color =
    region.severity === 'critical'
      ? COLORS.critical
      : region.severity === 'warning'
        ? COLORS.warning
        : COLORS.info
  return (
    <group position={region.position}>
      <mesh
        onClick={(event) => {
          event.stopPropagation()
          onSelect()
        }}
      >
        <sphereGeometry args={[active ? radius * 1.6 : radius, 16, 16]} />
        <meshBasicMaterial color={color} transparent opacity={active ? 0.9 : 0.55} />
      </mesh>
      {active && (
        <Html
          center
          position={[0, 0, radius * 2.4]}
          style={{
            color,
            font: '10px ui-monospace, monospace',
            whiteSpace: 'nowrap',
            pointerEvents: 'none',
          }}
        >
          {region.label}
        </Html>
      )}
    </group>
  )
}

function coneQuaternion(direction: Vector3) {
  // A cone points along +Y by default; rotate that onto the force direction.
  const quaternion = new Quaternion()
  quaternion.setFromUnitVectors(new Vector3(0, 1, 0), direction.clone().normalize())
  return quaternion
}
