package com.fairytrick.fairymahjong

import org.junit.Assert.*
import org.junit.Test

class TileCatalogTest {
    @Test fun catalogHasStableContiguousIdsAndPreservesLegacyMeanings() {
        assertEquals(62, TileCatalog.FACE_COUNT)
        assertEquals(TileCatalog.FACE_COUNT, TileCatalog.faces.size)
        assertEquals((0 until TileCatalog.FACE_COUNT).toList(), TileCatalog.faces.map { it.id })
        assertEquals(
            listOf("Bamboo", "Flower", "Sun", "Waves", "Leaf", "Star"),
            TileCatalog.faces.take(TileCatalog.LEGACY_FACE_COUNT).map { it.name },
        )
        TileCatalog.faces.take(TileCatalog.LEGACY_FACE_COUNT).forEach {
            assertNull(it.assetPath)
            assertNull(it.familyId)
        }
        TileCatalog.faces.forEach { assertSame(it, TileCatalog.face(it.id)) }
    }

    @Test fun eachFairyHasFourSeparateUniqueImagesInSavedIdOrder() {
        val fairyNames = listOf(
            "Teal Spell", "Fern Trickster", "Dragonfly Spark", "Cherry Blossom", "Acorn Scout",
            "Dewdrop", "Nightwink", "Honeybell", "Brambleberry", "Poppyzip", "Silvermoth",
            "Peachfizz", "Coppersong", "Velvet Fig",
        )
        val fairyFaces = TileCatalog.faces.drop(TileCatalog.LEGACY_FACE_COUNT)
        assertEquals(56, fairyFaces.size)
        assertEquals(56, fairyFaces.map { it.assetPath }.toSet().size)
        assertEquals(56, fairyFaces.map { it.name }.toSet().size)
        fairyFaces.chunked(4).forEachIndexed { offset, family ->
            val prefix = (offset + 1).toString().padStart(2, '0')
            assertTrue(family.all { it.familyId == offset + 1 })
            assertEquals(fairyNames[offset], family[0].name)
            assertEquals("${fairyNames[offset]} portrait", family[1].name)
            assertEquals("tiles/$prefix-fullbody.png", family[0].assetPath)
            assertEquals("tiles/$prefix-portrait.png", family[1].assetPath)
            assertTrue(family[2].assetPath!!.startsWith("tiles/$prefix-a-"))
            assertTrue(family[3].assetPath!!.startsWith("tiles/$prefix-b-"))
            assertTrue(family.all { it.assetPath!!.matches(Regex("tiles/[a-z0-9-]+\\.png")) })
        }
        assertEquals("Leaf Bow", TileCatalog.face(12).name)
        assertEquals("Starlight Diary", TileCatalog.face(32).name)
        assertEquals("Secret Letter", TileCatalog.face(61).name)
    }

    @Test fun invalidCatalogIdsAreRejectedInsteadOfDisplayingAnotherTile() {
        for (id in listOf(Int.MIN_VALUE, -1, TileCatalog.FACE_COUNT, Int.MAX_VALUE)) {
            assertThrows(IllegalArgumentException::class.java) { TileCatalog.face(id) }
        }
    }
}
