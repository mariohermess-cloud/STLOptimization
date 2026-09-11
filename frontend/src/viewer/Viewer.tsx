/**
 * The 3D viewer.
 *
 * Scene convention: Z-up, build plate on the XY plane, exactly like the
 * analysis. The part is drawn in its own coordinate system when no
 * orientation is selected, and in the recommended orientation - rotated and
 * dropped onto the plate - once the optimiser has produced one.
 *
 * Placement is expressed as `world = offset + R * part`, which is built in the
 * scene graph as an outer group carrying the offset and an inner group
 * carrying the rotation. Every overlay that lives in part coordinates (force
 * vectors, fixture markers, region markers) is nested the same way, so the
 * annotations follow the part into the recommended orientation.
 */

import { Html, OrbitControls } from '@react-three/drei'
import { Canvas, type ThreeEvent, useThree } from '@react-three/fiber'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Box3, Euler, Matrix4, type PerspectiveCamera, Vector3 } from 'three'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import { ApiError, api } from '../api/client'
import { selectedCandidate, useStore } from '../state/store'
import {
  Axes,
  BuildDirection,
  BuildPlate,
  COLORS,
  FixtureMarker,
  ForceArrow,
  LayerPlanes,
  RegionMarker,
} from './Overlays'
import { subsetGeometry, useModelGeometry } from './useModelGeometry'

export function Viewer() {
  const model = useStore((state) => state.model)
  const meshUrl = model ? api.meshUrl(model.id) : null
  const { data, error, loading } = useModelGeometry(meshUrl)
  const selectionMode = useStore((state) => state.selectionMode)
  const uploading = useStore((state) => state.uploading)
  const [resetToken, setResetToken] = useState(0)

  return (
    <div className="viewer">
      <ViewerToolbar onReset={() => setResetToken((token) => token + 1)} hasModel={!!data} />
      <Canvas
        dpr={[1, 2]}
        camera={{ position: [160, -160, 120], fov: 42, near: 0.5, far: 8000, up: [0, 0, 1] }}
        gl={{ antialias: true }}
        style={{ width: '100%', height: '100%' }}
      >
        <color attach="background" args={['#0a0e14']} />
        <hemisphereLight intensity={0.55} groundColor="#0a0e14" color="#cfe0f5" />
        <directionalLight position={[120, -160, 220]} intensity={1.5} />
        <directionalLight position={[-180, 120, 90]} intensity={0.5} />
        <Scene data={data} resetToken={resetToken} />
      </Canvas>

      {!model && !uploading && (
        <div className="viewer-empty">
          <div style={{ fontSize: 15 }}>No model loaded</div>
          <div>Upload an STL file to start the analysis.</div>
        </div>
      )}
      {(loading || uploading) && (
        <div className="viewer-empty">
          {uploading ? 'Uploading and analysing…' : 'Loading mesh…'}
        </div>
      )}
      {error && <div className="viewer-empty bad">{error}</div>}

      {model && <Legend />}
      {selectionMode !== 'none' && (
        <div className="viewer-hint">
          {selectionMode === 'fixation'
            ? 'Click a surface to mark it as fixed. Hold Shift to add further surfaces.'
            : 'Click a surface to apply the load there. Hold Shift to add further surfaces.'}
        </div>
      )}
    </div>
  )
}

function ViewerToolbar({ onReset, hasModel }: { onReset: () => void; hasModel: boolean }) {
  const overlays = useStore((state) => state.showOverlays)
  const toggle = useStore((state) => state.toggleOverlay)
  const result = useStore((state) => state.result)

  const entries: { key: keyof typeof overlays; label: string; enabled: boolean }[] = [
    { key: 'orientedPreview', label: 'Oriented', enabled: !!result },
    { key: 'buildDirection', label: 'Build dir', enabled: true },
    { key: 'layerPlane', label: 'Layers', enabled: true },
    { key: 'forces', label: 'Loads', enabled: true },
    { key: 'fixations', label: 'Fixed', enabled: true },
    { key: 'criticalRegions', label: 'Regions', enabled: !!result },
  ]

  return (
    <div className="viewer-toolbar">
      <button className="btn small" onClick={onReset} disabled={!hasModel}>
        Fit view
      </button>
      {entries.map((entry) => (
        <button
          key={entry.key}
          className={`btn small ${overlays[entry.key] ? 'active' : ''}`}
          onClick={() => toggle(entry.key)}
          disabled={!entry.enabled}
          title={`Toggle ${entry.label}`}
        >
          {entry.label}
        </button>
      ))}
    </div>
  )
}

function Legend() {
  return (
    <div className="viewer-legend">
      <span>
        <i className="swatch" style={{ background: COLORS.fixation }} /> Fixed surface
      </span>
      <span>
        <i className="swatch" style={{ background: COLORS.loadFace }} /> Loaded surface / force
      </span>
      <span>
        <i className="swatch" style={{ background: COLORS.buildDirection }} /> Build direction, layer
        plane
      </span>
      <span>
        <i className="swatch" style={{ background: COLORS.critical }} /> Potentially critical region
      </span>
    </div>
  )
}

type GeometryData = NonNullable<ReturnType<typeof useModelGeometry>['data']>

interface Placement {
  rotation: Euler
  offset: Vector3
  height: number
  span: number
}

function Scene({ data, resetToken }: { data: GeometryData | null; resetToken: number }) {
  const controls = useRef<OrbitControlsImpl>(null)
  const { camera, size } = useThree()
  const printers = useStore((state) => state.printers)
  const printerId = useStore((state) => state.process.printer_id)
  const overlays = useStore((state) => state.showOverlays)
  const result = useStore((state) => state.result)
  const candidate = useStore(selectedCandidate)
  const highlighted = useStore((state) => state.highlightedRegion)
  const setHighlighted = useStore((state) => state.setHighlightedRegion)
  const loadValidation = useStore((state) => state.loadValidation)

  const printer = printers.find((entry) => entry.id === printerId)
  const plate: [number, number] = printer
    ? [printer.bed_size_mm[0], printer.bed_size_mm[1]]
    : [256, 256]

  const oriented = overlays.orientedPreview && !!candidate

  const placement: Placement | null = useMemo(() => {
    if (!data?.geometry.boundingBox) return null
    const rotation = new Euler(0, 0, 0, 'XYZ')
    if (oriented && candidate) {
      rotation.set(
        (candidate.rotation_x * Math.PI) / 180,
        (candidate.rotation_y * Math.PI) / 180,
        (candidate.rotation_z * Math.PI) / 180,
        'XYZ',
      )
    }
    const matrix = new Matrix4().makeRotationFromEuler(rotation)
    const rotated = new Box3().setFromPoints(
      boxCorners(data.geometry.boundingBox).map((corner) => corner.applyMatrix4(matrix)),
    )
    return {
      rotation,
      offset: new Vector3(
        -(rotated.max.x + rotated.min.x) / 2,
        -(rotated.max.y + rotated.min.y) / 2,
        -rotated.min.z,
      ),
      height: rotated.max.z - rotated.min.z,
      span: Math.max(
        rotated.max.x - rotated.min.x,
        rotated.max.y - rotated.min.y,
        rotated.max.z - rotated.min.z,
      ),
    }
  }, [data, oriented, candidate])

  // Fit the camera when the model or the orientation changes, and on demand.
  // The distance follows from the field of view and the viewport aspect, so a
  // tall phone viewport pulls back far enough to keep the whole part visible.
  useEffect(() => {
    if (!placement || !controls.current) return
    const perspective = camera as PerspectiveCamera
    const radius = (placement.span * Math.sqrt(3)) / 2
    const fov = ((perspective.fov ?? 42) * Math.PI) / 180
    const aspect = size.height > 0 ? size.width / size.height : 1
    const horizontalFov = 2 * Math.atan(Math.tan(fov / 2) * Math.max(aspect, 0.2))
    const distance =
      Math.max(radius / Math.sin(fov / 2), radius / Math.sin(horizontalFov / 2)) * 1.15
    camera.position.set(distance * 0.62, -distance * 0.62, distance * 0.48)
    controls.current.target.set(0, 0, placement.height / 2)
    controls.current.update()
  }, [placement, camera, size.width, size.height, resetToken])

  const regions = result?.critical_regions.filter((region) => region.position) ?? []

  return (
    <>
      <BuildPlate size={plate} />
      <Axes length={Math.max(30, (placement?.span ?? 60) * 0.45)} />
      <OrbitControls
        ref={controls}
        makeDefault
        enableDamping
        dampingFactor={0.12}
        maxDistance={4000}
        minDistance={2}
      />

      {data && placement && (
        <group position={placement.offset}>
          <group rotation={placement.rotation}>
            <ModelMesh data={data} />

            {overlays.forces &&
              loadValidation?.load_paths.map((path) => (
                <ForceArrow
                  key={path.load_case_id}
                  path={path}
                  scale={Math.max(placement.span * 0.35, 8)}
                />
              ))}
            {overlays.fixations && loadValidation?.load_paths[0] && (
              <FixtureMarker position={loadValidation.load_paths[0].fixed_point} />
            )}

            {overlays.criticalRegions &&
              regions.map((region) => (
                <RegionMarker
                  key={region.id}
                  region={region}
                  active={highlighted?.id === region.id}
                  radius={Math.max(placement.span * 0.012, 0.5)}
                  onSelect={() => setHighlighted(highlighted?.id === region.id ? null : region)}
                />
              ))}
          </group>
        </group>
      )}

      {placement && overlays.buildDirection && <BuildDirection height={placement.height} />}
      {placement && overlays.layerPlane && oriented && (
        <LayerPlanes height={placement.height} span={placement.span * 1.4} />
      )}
    </>
  )
}

function ModelMesh({ data }: { data: GeometryData }) {
  const selectionMode = useStore((state) => state.selectionMode)
  const applySelection = useStore((state) => state.applyFaceSelection)
  const pushToast = useStore((state) => state.pushToast)
  const model = useStore((state) => state.model)
  const constraints = useStore((state) => state.constraints)
  const loads = useStore((state) => state.loads)
  const overlays = useStore((state) => state.showOverlays)
  const highlighted = useStore((state) => state.highlightedRegion)
  const [picking, setPicking] = useState(false)

  const fixedFaces = constraints[0]?.face_ids ?? []
  const loadedFaces = useMemo(
    () => Array.from(new Set(loads.flatMap((load) => load.application_face_ids))),
    [loads],
  )
  const regionFaces = highlighted?.face_ids ?? []

  const fixedGeometry = useMemo(
    () => (overlays.fixations ? subsetGeometry(data.geometry, fixedFaces) : null),
    [data.geometry, fixedFaces, overlays.fixations],
  )
  const loadedGeometry = useMemo(
    () => (overlays.forces ? subsetGeometry(data.geometry, loadedFaces) : null),
    [data.geometry, loadedFaces, overlays.forces],
  )
  const regionGeometry = useMemo(
    () => subsetGeometry(data.geometry, regionFaces),
    [data.geometry, regionFaces],
  )

  const handleClick = useCallback(
    async (event: ThreeEvent<MouseEvent>) => {
      if (selectionMode === 'none' || !model || picking) return
      event.stopPropagation()
      const faceIndex = event.faceIndex
      if (faceIndex === undefined || faceIndex === null) return
      setPicking(true)
      try {
        const region = await api.pickRegion(model.id, faceIndex)
        applySelection(region.face_ids, event.nativeEvent.shiftKey)
      } catch (cause) {
        pushToast('error', cause instanceof ApiError ? cause.message : String(cause))
      } finally {
        setPicking(false)
      }
    },
    [selectionMode, model, picking, applySelection, pushToast],
  )

  return (
    <group>
      <mesh
        geometry={data.geometry}
        onClick={handleClick}
        onPointerOver={() => {
          if (selectionMode !== 'none') document.body.style.cursor = 'crosshair'
        }}
        onPointerOut={() => {
          document.body.style.cursor = ''
        }}
      >
        <meshStandardMaterial color={COLORS.model} metalness={0.08} roughness={0.62} />
      </mesh>

      {fixedGeometry && (
        <mesh geometry={fixedGeometry}>
          <meshStandardMaterial
            color={COLORS.fixation}
            emissive={COLORS.fixation}
            emissiveIntensity={0.25}
            polygonOffset
            polygonOffsetFactor={-2}
          />
        </mesh>
      )}
      {loadedGeometry && (
        <mesh geometry={loadedGeometry}>
          <meshStandardMaterial
            color={COLORS.loadFace}
            emissive={COLORS.loadFace}
            emissiveIntensity={0.25}
            polygonOffset
            polygonOffsetFactor={-2}
          />
        </mesh>
      )}
      {regionGeometry && (
        <mesh geometry={regionGeometry}>
          <meshStandardMaterial
            color={COLORS.critical}
            emissive={COLORS.critical}
            emissiveIntensity={0.35}
            polygonOffset
            polygonOffsetFactor={-3}
          />
        </mesh>
      )}
      {picking && (
        <Html center style={{ color: '#97a4b4', font: '11px ui-monospace, monospace' }}>
          selecting…
        </Html>
      )}
    </group>
  )
}

function boxCorners(box: Box3): Vector3[] {
  const { min, max } = box
  return [
    new Vector3(min.x, min.y, min.z),
    new Vector3(max.x, min.y, min.z),
    new Vector3(min.x, max.y, min.z),
    new Vector3(max.x, max.y, min.z),
    new Vector3(min.x, min.y, max.z),
    new Vector3(max.x, min.y, max.z),
    new Vector3(min.x, max.y, max.z),
    new Vector3(max.x, max.y, max.z),
  ]
}
