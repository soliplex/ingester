import fs from 'fs';
import path from 'path';

/**
 * Post-build script to convert hash-based filenames to static filenames
 * with query string cache busting
 */

const buildDir = './build';
const versionFile = path.join(buildDir, '_app/version.json');
const htmlFile = path.join(buildDir, 'index.html');

// Read version
const version = JSON.parse(fs.readFileSync(versionFile, 'utf-8')).version;
console.log(`Build version: ${version}`);

// Recursively find all files with given extension
function findFiles(dir, extension) {
	const files = [];
	const entries = fs.readdirSync(dir, { withFileTypes: true });

	for (const entry of entries) {
		const fullPath = path.join(dir, entry.name);
		if (entry.isDirectory()) {
			files.push(...findFiles(fullPath, extension));
		} else if (entry.isFile() && entry.name.endsWith(extension)) {
			files.push(fullPath);
		}
	}

	return files;
}

// Find all JS and CSS files in immutable directory
const jsFiles = findFiles(path.join(buildDir, '_app/immutable'), '.js');
const cssFiles = findFiles(path.join(buildDir, '_app/immutable'), '.css');

const renamedFiles = new Map(); // hash-based name -> static name

// Identify and rename chunk files (vendor and app bundles)
const chunkDir = path.join(buildDir, '_app/immutable/chunks');
const chunkRenames = new Map(); // old hash name -> new static name

if (fs.existsSync(chunkDir)) {
	const chunkFiles = fs.readdirSync(chunkDir)
		.filter(f => f.endsWith('.js') && /^[A-Za-z0-9_-]{8,}\.js$/.test(f))
		.map(f => {
			const fullPath = path.join(chunkDir, f);
			const size = fs.statSync(fullPath).size;
			return { name: f, path: fullPath, size };
		})
		.sort((a, b) => b.size - a.size); // Sort by size descending

	// Typically: largest = vendor (node_modules), second = app
	if (chunkFiles.length >= 2) {
		const vendorFile = chunkFiles[0]; // Largest
		const appFile = chunkFiles[1];    // Second largest

		// Store chunk renames for content replacement
		const vendorHash = vendorFile.name.replace('.js', '');
		const appHash = appFile.name.replace('.js', '');
		chunkRenames.set(vendorHash, 'vendor');
		chunkRenames.set(appHash, 'app');

		// Store for HTML updates
		const vendorOldUrl = `/_app/immutable/chunks/${vendorFile.name}`;
		const vendorNewUrl = '/_app/immutable/chunks/vendor.js';
		renamedFiles.set(vendorOldUrl, vendorNewUrl);

		const appOldUrl = `/_app/immutable/chunks/${appFile.name}`;
		const appNewUrl = '/_app/immutable/chunks/app.js';
		renamedFiles.set(appOldUrl, appNewUrl);

		// Update import references in ALL JavaScript files before renaming
		// This includes: chunk files, node files, and entry files
		const allJsFiles = findFiles(path.join(buildDir, '_app/immutable'), '.js');

		for (const [oldHash, newName] of chunkRenames) {
			for (const jsFile of allJsFiles) {
				let content = fs.readFileSync(jsFile, 'utf-8');
				// Replace import references: from"./OldHash.js" -> from"./newName.js" (same directory)
				const regexSameDir = new RegExp(`(from\\s*["'])\\.\/${oldHash}(\\.js["'])`, 'g');
				content = content.replace(regexSameDir, `$1./${newName}$2`);

				// Replace import references: from"../chunks/OldHash.js" -> from"../chunks/newName.js" (from nodes/entry)
				const regexParentDir = new RegExp(`(from\\s*["'])\\.\\.\\/chunks\\/${oldHash}(\\.js["'])`, 'g');
				content = content.replace(regexParentDir, `$1../chunks/${newName}$2`);

				fs.writeFileSync(jsFile, content);
			}
		}

		// Now rename the files
		const vendorNewPath = path.join(chunkDir, 'vendor.js');
		fs.renameSync(vendorFile.path, vendorNewPath);
		console.log(`Renamed: ${vendorFile.name} -> vendor.js (vendor bundle, ${(vendorFile.size / 1024).toFixed(1)}KB)`);

		const appNewPath = path.join(chunkDir, 'app.js');
		fs.renameSync(appFile.path, appNewPath);
		console.log(`Renamed: ${appFile.name} -> app.js (app bundle, ${(appFile.size / 1024).toFixed(1)}KB)`);
	}
}

jsFiles.forEach(file => {
	const relativePath = path.relative(buildDir, file);
	const fileName = path.basename(file);
	const dirName = path.dirname(file);

	// Extract name without hash: app.C8s3uU_i.js -> app
	// Also handles: 2oOcqMSm.js -> 2oOcqMSm (keep hash-only names as-is)
	// Pattern: [name].[hash].js OR [hash].js
	const matchWithName = fileName.match(/^(.+?)\.([A-Za-z0-9_-]{8,})\.js$/);
	const matchHashOnly = fileName.match(/^([A-Za-z0-9_-]{8,})\.js$/);

	let staticName, newPath;

	if (matchWithName) {
		// Has name prefix: app.C8s3uU_i.js -> app.js
		const [, baseName] = matchWithName;
		staticName = `${baseName}.js`;
		newPath = path.join(dirName, staticName);
	} else if (matchHashOnly) {
		// Hash-only name: keep as-is (chunks)
		staticName = fileName;
		newPath = file;
	}

	if (staticName && file !== newPath) {
		// Store mapping for HTML update BEFORE renaming (use forward slashes for URL)
		const relativeFromApp = path.relative(path.join(buildDir, '_app'), file).replace(/\\/g, '/');
		const relativeNewFromApp = path.relative(path.join(buildDir, '_app'), newPath).replace(/\\/g, '/');

		const oldUrl = `/_app/${relativeFromApp}`;
		const newUrl = `/_app/${relativeNewFromApp}`;

		renamedFiles.set(oldUrl, newUrl);

		// Rename file
		fs.renameSync(file, newPath);

		console.log(`Renamed: ${fileName} -> ${staticName}`);
	}
});

// Process CSS files
cssFiles.forEach(file => {
	const relativePath = path.relative(buildDir, file);
	const fileName = path.basename(file);
	const dirName = path.dirname(file);

	// Extract name without hash: app.CqS_Wi5k.css -> app
	// Pattern: [name].[hash].css
	const matchWithName = fileName.match(/^(.+?)\.([A-Za-z0-9_-]{8,})\.css$/);

	if (matchWithName) {
		// Has name prefix: app.CqS_Wi5k.css -> app.css
		const [, baseName] = matchWithName;
		const staticName = `${baseName}.css`;
		const newPath = path.join(dirName, staticName);

		// Store mapping for HTML update BEFORE renaming (use forward slashes for URL)
		const relativeFromApp = path.relative(path.join(buildDir, '_app'), file).replace(/\\/g, '/');
		const relativeNewFromApp = path.relative(path.join(buildDir, '_app'), newPath).replace(/\\/g, '/');

		const oldUrl = `/_app/${relativeFromApp}`;
		const newUrl = `/_app/${relativeNewFromApp}`;

		renamedFiles.set(oldUrl, newUrl);

		// Rename file
		fs.renameSync(file, newPath);

		console.log(`Renamed: ${fileName} -> ${staticName}`);
	}
});

// Update HTML file
let html = fs.readFileSync(htmlFile, 'utf-8');

// Replace all references to renamed files
renamedFiles.forEach((newUrl, oldUrl) => {
	// Add version query string
	const newUrlWithVersion = `${newUrl}?v=${version}`;
	html = html.replaceAll(oldUrl, newUrlWithVersion);
});

fs.writeFileSync(htmlFile, html);

console.log(`\nUpdated ${htmlFile} with static filenames and version query strings`);
console.log(`Total files renamed: ${renamedFiles.size}`);

// List final files by category
console.log(`\n=== Build Summary ===`);

// Chunk files
console.log(`\nChunk files (bundles):`);
const chunkPath = path.join(buildDir, '_app/immutable/chunks');
if (fs.existsSync(chunkPath)) {
	const chunks = fs.readdirSync(chunkPath).filter(f => f.endsWith('.js'));
	chunks.forEach(file => {
		const fullPath = path.join(chunkPath, file);
		const stats = fs.statSync(fullPath);
		const sizeKB = (stats.size / 1024).toFixed(2);
		console.log(`  ${file.padEnd(20)} ${sizeKB.padStart(8)} KB`);
	});
}

// Entry files
console.log(`\nEntry files:`);
const entryPath = path.join(buildDir, '_app/immutable/entry');
if (fs.existsSync(entryPath)) {
	const entries = fs.readdirSync(entryPath).filter(f => f.endsWith('.js'));
	entries.forEach(file => {
		const fullPath = path.join(entryPath, file);
		const stats = fs.statSync(fullPath);
		const sizeKB = (stats.size / 1024).toFixed(2);
		console.log(`  ${file.padEnd(20)} ${sizeKB.padStart(8)} KB`);
	});
}

// Node files (route entries)
console.log(`\nRoute nodes:`);
const nodePath = path.join(buildDir, '_app/immutable/nodes');
if (fs.existsSync(nodePath)) {
	const nodes = fs.readdirSync(nodePath).filter(f => f.endsWith('.js'));
	console.log(`  ${nodes.length} files (${nodes.join(', ')}) ~${(nodes.length * 0.09).toFixed(2)} KB total`);
}

// CSS files
console.log(`\nCSS files:`);
const finalCssFiles = findFiles(path.join(buildDir, '_app/immutable'), '.css');
finalCssFiles.forEach(file => {
	const stats = fs.statSync(file);
	const sizeKB = (stats.size / 1024).toFixed(2);
	const fileName = path.basename(file);
	console.log(`  ${fileName.padEnd(20)} ${sizeKB.padStart(8)} KB`);
});

// Total count
const totalJsFiles = findFiles(path.join(buildDir, '_app/immutable'), '.js').length;
console.log(`\n=== Totals ===`);
console.log(`JavaScript files: ${totalJsFiles}`);
console.log(`CSS files: ${finalCssFiles.length}`);
