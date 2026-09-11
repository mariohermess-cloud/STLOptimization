/**
 * End-to-end check of the primary user workflow, driven through a real
 * browser against the built frontend and the running API.
 *
 *   open app -> upload STL -> model appears in 3D -> geometry analysed ->
 *   material -> fixed surface -> load case -> optimise -> top orientations ->
 *   score breakdown -> reasons -> print settings -> export JSON
 *
 * Usage: node scripts/e2e_check.mjs [baseUrl] [stlPath]
 */

import { chromium } from 'playwright'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.argv[2] ?? 'http://127.0.0.1:4173'
const STL = process.argv[3] ?? 'test-data/bracket.stl'

const steps = []
let failures = 0

function check(name, condition, detail = '') {
  const ok = Boolean(condition)
  if (!ok) failures += 1
  steps.push(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`)
  return ok
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function main() {
  if (!existsSync(STL)) throw new Error(`Missing fixture ${STL}`)
  const downloadDir = mkdtempSync(join(tmpdir(), 'peo-e2e-'))

  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined,
    args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
  })
  const context = await browser.newContext({
    viewport: { width: 1600, height: 950 },
    acceptDownloads: true,
  })
  const page = await context.newPage()

  const consoleErrors = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => consoleErrors.push(String(error)))

  await page.goto(BASE, { waitUntil: 'networkidle' })
  check('application loads', await page.locator('h1', { hasText: 'PRINT ENGINEERING' }).count())
  check('catalogue reached the browser', (await page.locator('select').first().count()) > 0)

  // --- upload ---------------------------------------------------------
  await page.setInputFiles('input[type=file]', STL)
  await page.waitForSelector('text=Model information', { timeout: 60000 })
  const dimensions = await page.locator('dd').first().innerText()
  check('geometry analysed and displayed', /mm/.test(dimensions), dimensions)
  check(
    'canvas rendered',
    (await page.locator('canvas').count()) > 0,
    `${await page.locator('canvas').count()} canvas`,
  )

  // --- material -------------------------------------------------------
  const materialSelect = page.locator('select').filter({ hasText: 'PETG' }).first()
  await materialSelect.selectOption('pa-cf')
  await wait(400)
  check('material selectable', (await materialSelect.inputValue()) === 'pa-cf')

  // --- fixation (via the viewer) --------------------------------------
  await page.getByRole('button', { name: 'Pick in viewer' }).click()
  const canvas = page.locator('canvas').first()
  const box = await canvas.boundingBox()
  // Click several points until the API returns a face region.
  let fixedPicked = false
  for (const [dx, dy] of [
    [0.5, 0.62],
    [0.45, 0.55],
    [0.55, 0.68],
    [0.5, 0.5],
    [0.42, 0.7],
  ]) {
    await page.mouse.click(box.x + box.width * dx, box.y + box.height * dy)
    await wait(900)
    const badge = await page.locator('.panel-header', { hasText: 'Fixed surfaces' }).innerText()
    if (/\d+ faces/.test(badge)) {
      fixedPicked = true
      break
    }
  }
  check('fixed surface selected by clicking the model', fixedPicked)

  // --- load case -------------------------------------------------------
  await page.getByRole('button', { name: '+ Bending' }).click()
  await wait(300)
  const loadBadge = await page.locator('.panel-header', { hasText: 'Load cases' }).innerText()
  check('load case added', /1/.test(loadBadge), loadBadge)

  // --- optimise --------------------------------------------------------
  const optimise = page.getByRole('button', { name: 'Optimise orientation' })
  check('optimise enabled once inputs are complete', await optimise.isEnabled())
  await optimise.click()
  await page.waitForSelector('text=Score breakdown', { timeout: 240000 })

  const overall = await page.locator('.score-hero .value').innerText()
  check('overall score shown', /^\d+$/.test(overall.trim()), overall)

  const candidateCount = await page.locator('.candidate').count()
  check('top orientations listed', candidateCount >= 1, `${candidateCount} candidates`)

  const componentRows = await page.locator('.score-row').count()
  check('score breakdown is itemised', componentRows >= 8, `${componentRows} rows`)

  const reasons = await page.locator('.reasons li').count()
  check('reasoning shown', reasons > 0, `${reasons} statements`)

  const confidence = await page
    .locator('.panel-header', { hasText: 'Analysis confidence' })
    .innerText()
  check('confidence reported', /%/.test(confidence), confidence.trim())

  await page.waitForSelector('text=Recommended print settings', { timeout: 60000 })
  const settingsText = await page
    .locator('.panel', { hasText: 'Recommended print settings' })
    .innerText()
  check('print settings recommended', /Layer height/.test(settingsText))
  check('wall vs infill explained', /second moment of area/.test(settingsText))

  // --- comparison -------------------------------------------------------
  if (candidateCount > 1) {
    await page.locator('.candidate').nth(1).locator('button.compare').click()
    await wait(400)
    check('orientation comparison renders', (await page.locator('table.compare').count()) > 0)
  }

  // --- critical regions --------------------------------------------------
  const regionPanel = await page
    .locator('.panel', { hasText: 'Potentially critical geometric regions' })
    .innerText()
  check('critical regions section present', regionPanel.length > 0)

  // --- export ------------------------------------------------------------
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 30000 }),
    page.getByRole('button', { name: 'Print settings (JSON)' }).click(),
  ])
  const target = join(downloadDir, download.suggestedFilename())
  await download.saveAs(target)
  const exported = JSON.parse(readFileSync(target, 'utf8'))
  check(
    'export contains calculated values',
    typeof exported.layer_height === 'number' &&
      typeof exported.wall_loops === 'number' &&
      typeof exported.infill_density === 'number' &&
      exported.orientation &&
      typeof exported.orientation.x === 'number',
    JSON.stringify({
      layer_height: exported.layer_height,
      wall_loops: exported.wall_loops,
      infill: exported.infill_density,
      orientation: exported.orientation,
    }),
  )
  check('export carries the disclaimer', typeof exported.disclaimer === 'string')

  const [reportDownload] = await Promise.all([
    page.waitForEvent('download', { timeout: 30000 }),
    page.getByRole('button', { name: 'Full analysis report (JSON)' }).click(),
  ])
  const reportPath = join(downloadDir, reportDownload.suggestedFilename())
  await reportDownload.saveAs(reportPath)
  const report = JSON.parse(readFileSync(reportPath, 'utf8')).report
  check(
    'analysis report is complete',
    report &&
      report.model &&
      report.material &&
      report.loads &&
      report.constraints &&
      report.selected_orientation &&
      report.confidence &&
      report.limitations?.length > 0,
  )

  // --- responsive --------------------------------------------------------
  await page.setViewportSize({ width: 390, height: 844 })
  await wait(700)
  const tabs = await page.locator('.mobile-tabs button').count()
  check('mobile tabs appear on a phone viewport', tabs === 3, `${tabs} tabs`)
  await page.locator('.mobile-tabs button', { hasText: 'Model' }).click()
  await wait(500)
  const viewerBox = await page.locator('.viewer').boundingBox()
  check(
    'viewer stays usable on a phone',
    viewerBox && viewerBox.height > 300 && viewerBox.width <= 390,
    viewerBox ? `${Math.round(viewerBox.width)}x${Math.round(viewerBox.height)}` : 'missing',
  )
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  check('no horizontal overflow on a phone', overflow <= 1, `${overflow}px`)

  await page.screenshot({ path: join(downloadDir, 'mobile.png') })
  await page.setViewportSize({ width: 1600, height: 950 })
  await wait(600)
  await page.screenshot({ path: join(downloadDir, 'desktop.png'), fullPage: false })

  check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '))

  await browser.close()

  console.log(steps.join('\n'))
  console.log(`\nscreenshots: ${downloadDir}`)
  console.log(failures === 0 ? '\nALL CHECKS PASSED' : `\n${failures} CHECK(S) FAILED`)
  process.exit(failures === 0 ? 0 : 1)
}

main().catch((error) => {
  console.error(steps.join('\n'))
  console.error(error)
  process.exit(1)
})
