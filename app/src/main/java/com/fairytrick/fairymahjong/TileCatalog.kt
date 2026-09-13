package com.fairytrick.fairymahjong

import java.util.Collections

/** Each image is its own matching identity; a fairy and her belongings do not cross-match. */
internal data class TileFace(
    val id: Int,
    val name: String,
    val assetPath: String?,
    val familyId: Int?,
)

/** IDs are saved with the deal. Keep existing entries and their meanings stable. */
internal object TileCatalog {
    const val LEGACY_FACE_COUNT = 6
    const val FACE_COUNT = 62

    val faces: List<TileFace> = Collections.unmodifiableList(listOf(
        TileFace(0, "Bamboo", null, null),
        TileFace(1, "Flower", null, null),
        TileFace(2, "Sun", null, null),
        TileFace(3, "Waves", null, null),
        TileFace(4, "Leaf", null, null),
        TileFace(5, "Star", null, null),
        TileFace(6, "Teal Spell", "tiles/01-fullbody.png", 1),
        TileFace(7, "Teal Spell portrait", "tiles/01-portrait.png", 1),
        TileFace(8, "Moon-Tip Hat", "tiles/01-a-floppy-hat.png", 1),
        TileFace(9, "Mint Star Wand", "tiles/01-b-star-wand.png", 1),
        TileFace(10, "Fern Trickster", "tiles/02-fullbody.png", 2),
        TileFace(11, "Fern Trickster portrait", "tiles/02-portrait.png", 2),
        TileFace(12, "Leaf Bow", "tiles/02-a-leaf-bow.png", 2),
        TileFace(13, "Mushroom Parasol", "tiles/02-b-mushroom-parasol.png", 2),
        TileFace(14, "Dragonfly Spark", "tiles/03-fullbody.png", 3),
        TileFace(15, "Dragonfly Spark portrait", "tiles/03-portrait.png", 3),
        TileFace(16, "Dragonfly Goggles", "tiles/03-a-racing-goggles.png", 3),
        TileFace(17, "Whirlwind Pinwheel", "tiles/03-b-pinwheel.png", 3),
        TileFace(18, "Cherry Blossom", "tiles/04-fullbody.png", 4),
        TileFace(19, "Cherry Blossom portrait", "tiles/04-portrait.png", 4),
        TileFace(20, "Blossom Fan", "tiles/04-a-folding-fan.png", 4),
        TileFace(21, "Petal Perfume", "tiles/04-b-petal-perfume.png", 4),
        TileFace(22, "Acorn Scout", "tiles/05-fullbody.png", 5),
        TileFace(23, "Acorn Scout portrait", "tiles/05-portrait.png", 5),
        TileFace(24, "Acorn Satchel", "tiles/05-a-acorn-satchel.png", 5),
        TileFace(25, "Woodland Compass", "tiles/05-b-pocket-compass.png", 5),
        TileFace(26, "Dewdrop", "tiles/06-fullbody.png", 6),
        TileFace(27, "Dewdrop portrait", "tiles/06-portrait.png", 6),
        TileFace(28, "Cloud-Cozy Scarf", "tiles/06-a-cozy-scarf.png", 6),
        TileFace(29, "Cloud Teacup", "tiles/06-b-cloud-teacup.png", 6),
        TileFace(30, "Nightwink", "tiles/07-fullbody.png", 7),
        TileFace(31, "Nightwink portrait", "tiles/07-portrait.png", 7),
        TileFace(32, "Starlight Diary", "tiles/07-a-secret-diary.png", 7),
        TileFace(33, "Moth Lantern", "tiles/07-b-moth-lantern.png", 7),
        TileFace(34, "Honeybell", "tiles/08-fullbody.png", 8),
        TileFace(35, "Honeybell portrait", "tiles/08-portrait.png", 8),
        TileFace(36, "Pocket Honey Pot", "tiles/08-a-honey-pot.png", 8),
        TileFace(37, "Ribbon Bell", "tiles/08-b-ribbon-bell.png", 8),
        TileFace(38, "Brambleberry", "tiles/09-fullbody.png", 9),
        TileFace(39, "Brambleberry portrait", "tiles/09-portrait.png", 9),
        TileFace(40, "Berry Basket", "tiles/09-a-berry-basket.png", 9),
        TileFace(41, "Jam Spoon", "tiles/09-b-jam-spoon.png", 9),
        TileFace(42, "Poppyzip", "tiles/10-fullbody.png", 10),
        TileFace(43, "Poppyzip portrait", "tiles/10-portrait.png", 10),
        TileFace(44, "Poppy Kite", "tiles/10-a-poppy-kite.png", 10),
        TileFace(45, "Breeze Boots", "tiles/10-b-breeze-boots.png", 10),
        TileFace(46, "Silvermoth", "tiles/11-fullbody.png", 11),
        TileFace(47, "Silvermoth portrait", "tiles/11-portrait.png", 11),
        TileFace(48, "Silver Hand Mirror", "tiles/11-a-hand-mirror.png", 11),
        TileFace(49, "Coral Ribbon Spool", "tiles/11-b-ribbon-spool.png", 11),
        TileFace(50, "Peachfizz", "tiles/12-fullbody.png", 12),
        TileFace(51, "Peachfizz portrait", "tiles/12-portrait.png", 12),
        TileFace(52, "Peach Fizz Bottle", "tiles/12-a-fizz-bottle.png", 12),
        TileFace(53, "Petal Comb", "tiles/12-b-petal-comb.png", 12),
        TileFace(54, "Coppersong", "tiles/13-fullbody.png", 13),
        TileFace(55, "Coppersong portrait", "tiles/13-portrait.png", 13),
        TileFace(56, "Pocket Trumpet", "tiles/13-a-pocket-trumpet.png", 13),
        TileFace(57, "Teal Tambourine", "tiles/13-b-teal-tambourine.png", 13),
        TileFace(58, "Velvet Fig", "tiles/14-fullbody.png", 14),
        TileFace(59, "Velvet Fig portrait", "tiles/14-portrait.png", 14),
        TileFace(60, "Velvet Earmuffs", "tiles/14-a-velvet-earmuffs.png", 14),
        TileFace(61, "Secret Letter", "tiles/14-b-secret-letter.png", 14),
    ))

    fun face(id: Int): TileFace {
        require(id in faces.indices) { "Invalid tile face" }
        return faces[id]
    }
}
