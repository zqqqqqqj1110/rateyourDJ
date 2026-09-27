# 阶段 2 召回评估

- 索引：`idx-catalog-20260926-baai-bge-m3-doc-v2`（BAAI/bge-m3）
- 查询集：eval/retrieval_queries_v1.jsonl（13 条）
- 相关性裁判：vec（兴趣中心向量）、tag（种子标签重合），“相关”= 该裁判下曲库前 15%

## 汇总（各查询平均）

| 指标 | rule_only | rag |
|---|---|---|
| branch_accuracy_tag | 0.997 | 0.977 |
| branch_accuracy_vec | 0.957 | 0.977 |
| relevant_share_tag | 0.941 | 0.949 |
| relevant_share_vec | 0.431 | 0.841 |
| relevant_tail_tag | 10.692 | 11.385 |
| relevant_tail_vec | 1.462 | 9.769 |
| tail_share | 0.374 | 0.387 |
| unique_artist_share | 0.587 | 0.575 |
| branch_overlap_jaccard | 0.0 | 0.0 |
| hub_songs | 6 | 12 |
| max_song_frequency | 8 | 9 |
| all_traceable | True | True |
| all_evidence_valid | True | True |
| exploration → tail_share | {'explore-0.1': 0.233, 'explore-0.5': 0.367, 'explore-0.9': 0.5} | {'explore-0.1': 0.233, 'explore-0.5': 0.367, 'explore-0.9': 0.5} |

## 样例（每个查询前 10）

### pf-text-zh
**rule_only**
1. [tail/tail] Roger Waters - Get Your Filthy Hands Off My Desert / Southampton Dock
2. [tail/tail] Spirit - It’s Time Now
3. [tail/tail] Spirit - Mega Star
4. [tail/tail] Porcupine Tree - Not Beautiful Anymore / Siren / Small Fish
5. [tail/tail] Hawkwind - Spiral Galaxy
6. [tail/tail] Hawkwind - Our Lives Can’t Last Forever
7. [tail/tail] Daevid Allen and Mother Gong - Unseen Ally
8. [tail/tail] Daevid Allen and Mother Gong - La dea madri
9. [tail/tail] Пикник и Вадим Самойлов - Теперь ты
10. [tail/tail] Пикник и Вадим Самойлов - Бисер у ног
**rag**
1. [tail/tail] Spirit - Love Tonight
2. [tail/tail] Spirit - Monkey See Monkey Do
3. [tail/tail] Hawkwind - What’s the Matter
4. [tail/tail] Hawkwind - Diamond Ring
5. [tail/tail] Gong - Red Alert
6. [tail/tail] King Crimson - Silent Night
7. [tail/tail] Gong - Percolations, Parts 1 & 2
8. [tail/tail] Procol Harum - Souvenir of London
9. [tail/tail] The Moody Blues - The Sunset
10. [tail/tail] Roger Waters - Get Your Filthy Hands Off My Desert / Southampton Dock

### pf-text-en
**rule_only**
1. [tail/tail] Yes - Don’t Take No for an Answer
2. [tail/tail] Yes - Ritual
3. [tail/tail] Gentle Giant - Features From Octopus
4. [tail/tail] Gentle Giant - Aspiration
5. [tail/tail] The Alan Parsons Project - Extract 1 From “The Alan Parsons Project Audio Guide”
6. [tail/tail] Nektar - Shangri-La
7. [tail/tail] Nektar - Summer Breeze
8. [tail/tail] Electric Light Orchestra - Losing You
9. [tail/tail] Marillion - Were There Postmen?
10. [tail/tail] Marillion - Born to End
**rag**
1. [tail/tail] King Crimson - Silent Night
2. [tail/tail] Procol Harum - Souvenir of London
3. [tail/tail] Spirit - Love Tonight
4. [tail/tail] Spirit - Monkey See Monkey Do
5. [tail/tail] Emerson, Lake & Palmer - Knife-Edge
6. [tail/tail] Rick Wakeman - The Maker
7. [tail/tail] King Crimson - Devil Dogs of Tessellation Row
8. [tail/tail] Пикник - Караван
9. [tail/tail] Yes - Don’t Take No for an Answer
10. [tail/tail] The Alan Parsons Project - The Turn of a Friendly Card: I. The Turn of a Friendly Card, Part One / II. Snake Eyes / III. The Ace of Swords / IV. Nothing Left to Lose / V. The Turn of a Friendly Card, Part Two

### pf-genre-zh
**rule_only**
1. [tail/tail] King Crimson - Krim 3
2. [tail/tail] King Crimson - Inner Garden (complete)
3. [tail/tail] Nektar - After the Fall
4. [tail/tail] Yes - Ritual
5. [tail/tail] Gentle Giant - Features From Octopus
6. [tail/tail] Gentle Giant - Aspiration
7. [tail/tail] The Alan Parsons Project - Extract 1 From “The Alan Parsons Project Audio Guide”
8. [tail/tail] Yes - Don’t Take No for an Answer
9. [tail/tail] Nektar - Shangri-La
10. [tail/tail] Пикник - Самый громкий крик - тишина
**rag**
1. [tail/tail] Steve Hackett - A Life in Movies
2. [tail/tail] Rick Wakeman - The Maker
3. [tail/tail] Procol Harum - Souvenir of London
4. [tail/tail] Rick Wakeman - The Lord’s Prayer
5. [tail/tail] Steve Hackett - The Chamber of 32 Doors
6. [tail/tail] The Alan Parsons Project - The Turn of a Friendly Card: I. The Turn of a Friendly Card, Part One / II. Snake Eyes / III. The Ace of Swords / IV. Nothing Left to Lose / V. The Turn of a Friendly Card, Part Two
7. [tail/tail] King Crimson - Silent Night
8. [tail/tail] Hawkwind - What’s the Matter
9. [tail/tail] Hawkwind - Diamond Ring
10. [tail/tail] Anthony Phillips - Rule Britannia Closing Theme (1981)

### pf-hint
**rule_only**
1. [tail/tail] Пикник и Вадим Самойлов - Теперь ты
2. [tail/tail] Пикник и Вадим Самойлов - Бисер у ног
3. [tail/tail] Gentle Giant - Band Intro
4. [tail/tail] King Crimson - Silent Night
5. [tail/tail] Yes - Ritual
6. [tail/tail] King Crimson - Devil Dogs of Tessellation Row
7. [tail/tail] Marillion - Living in F E A R
8. [tail/tail] Marillion - El Dorado: iii. Demolished Lives
9. [tail/tail] The Alan Parsons Project - Extract 1 From “The Alan Parsons Project Audio Guide”
10. [tail/tail] Nektar - After the Fall
**rag**
1. [tail/tail] Spirit - Love Tonight
2. [tail/tail] King Crimson - Silent Night
3. [tail/tail] Spirit - Monkey See Monkey Do
4. [tail/tail] Procol Harum - Souvenir of London
5. [tail/tail] Emerson, Lake & Palmer - Knife-Edge
6. [tail/tail] Rick Wakeman - The Maker
7. [tail/tail] The Alan Parsons Project - The Turn of a Friendly Card: I. The Turn of a Friendly Card, Part One / II. Snake Eyes / III. The Ace of Swords / IV. Nothing Left to Lose / V. The Turn of a Friendly Card, Part Two
8. [tail/tail] Rick Wakeman - The Lord’s Prayer
9. [tail/tail] Roger Waters - Get Your Filthy Hands Off My Desert / Southampton Dock
10. [tail/tail] Пикник - Караван

### oasis-text-zh
**rule_only**
1. [tail/tail] Cast - Love You Like I Do
2. [tail/tail] Cast - Time Is Like a River
3. [tail/tail] Shed Seven - If The Music Don't Move Yer
4. [tail/tail] Embrace - We Are It
5. [tail/tail] Shed Seven & Laura McClure - Tripping With You
6. [tail/tail] The La’s - That’ll Be the Day (BBC2 The Late Show Feb ’89)
7. [tail/tail] Embrace - If You Feel Like a Sinner
8. [tail/tail] Paul Weller - Cosmic Fringes
9. [tail/tail] Paul Weller - Moving Canvas
10. [tail/tail] The Bluetones - Beat on the Brat
**rag**
1. [tail/tail] Embrace - If You Feel Like a Sinner
2. [tail/tail] James - Goal Goal Goal
3. [tail/tail] Embrace - A Tap on Your Shoulder
4. [tail/tail] James - Wisdom of the Throat
5. [tail/tail] Dodgy - Live Break
6. [tail/tail] Dodgy - This Is Ours
7. [tail/tail] Ash - Wild Surf (extended)
8. [tail/tail] The La’s - I Did the Painting
9. [tail/tail] Supergrass - Sometimes We’re Very Sad
10. [tail/tail] Mansun - Mansun’s Only Live Song

### oasis-text-en
**rule_only**
1. [tail/tail] The Pansies - Barely There
2. [tail/tail] The Pansies - Labels
3. [tail/tail] Dodgy - You Give Drugs a Bad Name
4. [tail/tail] Dodgy - Are You the One
5. [tail/tail] Shed Seven - If The Music Don't Move Yer
6. [tail/tail] Suede - What Am I Without You?
7. [tail/tail] Shed Seven & Laura McClure - Tripping With You
8. [tail/tail] Embrace - We Are It
9. [tail/tail] Suede - Chalk Circles
10. [tail/tail] Ocean Colour Scene - Me, I'm Left Unsure
**rag**
1. [tail/tail] Embrace - If You Feel Like a Sinner
2. [tail/tail] Supergrass - Sometimes We’re Very Sad
3. [tail/tail] Ash - Wild Surf (extended)
4. [tail/tail] James - Goal Goal Goal
5. [tail/tail] Subcircus - She Ain’t Heavy
6. [tail/tail] Subcircus - Shelley's on the Telephone
7. [tail/tail] Manic Street Preachers - If You Tolerate This
8. [tail/tail] Mansun - Mansun’s Only Live Song
9. [tail/tail] Dodgy - (Your Love Keeps Lifting Me) Higher and Higher
10. [tail/tail] Dodgy - Speaking in Tongues

### oasis-hint
**rule_only**
1. [tail/tail] Embrace - If You Feel Like a Sinner
2. [tail/tail] Embrace - Today
3. [tail/tail] Dodgy - You Give Drugs a Bad Name
4. [tail/tail] Dodgy - Are You the One
5. [tail/tail] Suede - What Am I Without You?
6. [tail/tail] Suede - Chalk Circles
7. [tail/tail] Paul Weller - Baptiste
8. [tail/tail] Paul Weller - Walkin’
9. [tail/tail] Ocean Colour Scene - Me, I'm Left Unsure
10. [tail/tail] Shed Seven - The Eye in the Sky
**rag**
1. [tail/tail] Embrace - If You Feel Like a Sinner
2. [tail/tail] James - Goal Goal Goal
3. [tail/tail] Ash - Wild Surf (extended)
4. [tail/tail] James - Wisdom of the Throat
5. [tail/tail] Supergrass - Sometimes We’re Very Sad
6. [tail/tail] Embrace - A Tap on Your Shoulder
7. [tail/tail] Manic Street Preachers - If You Tolerate This
8. [tail/tail] Mansun - Mansun’s Only Live Song
9. [tail/tail] Dodgy - Live Break
10. [tail/tail] Dodgy - This Is Ours

### both-between
**rule_only**
1. [tail/tail] Mansun - An Open Letter to the Lyrical Trainspotter
2. [tail/tail] Mansun - Moronica
3. [tail/tail] Amplifier - Gateway
4. [tail/tail] Amplifier - Entity
5. [tail/tail] Dodgy - You Give Drugs a Bad Name
6. [tail/tail] Dodgy - Are You the One
7. [tail/tail] Yes - Ritual
8. [tail/tail] Liam Gallagher - Sad Song
9. [tail/tail] Liam Gallagher - Supersonic
10. [tail/tail] Пикник и Вадим Самойлов - Теперь ты
**rag**
1. [tail/tail] Mansun - Mansun’s Only Live Song
2. [tail/tail] Embrace - A Tap on Your Shoulder
3. [tail/tail] James - Goal Goal Goal
4. [tail/tail] Embrace - I Know What's Going On
5. [tail/tail] Ash - Wild Surf (extended)
6. [tail/tail] Supergrass - Sometimes We’re Very Sad
7. [tail/tail] Procol Harum - Souvenir of London
8. [tail/tail] Spirit - Love Tonight
9. [tail/tail] Spirit - Monkey See Monkey Do
10. [tail/tail] Marillion - Goodbye to All That: (i) Wave / (ii) Mad / (iii) The Opium Den / (iv) The Slide / (v) Standing in the Swing

### both-psych-brit
**rule_only**
1. [tail/tail] Kula Shaker - Dead Crusader
2. [tail/tail] The Pansies - Barely There
3. [tail/tail] The Pansies - Labels
4. [tail/tail] Noel Gallagher’s High Flying Birds - God Help Us All
5. [tail/tail] Embrace - We Are It
6. [tail/tail] Dodgy - You Give Drugs a Bad Name
7. [tail/tail] Dodgy - Are You the One
8. [tail/tail] Embrace - If You Feel Like a Sinner
9. [tail/tail] James - Leviathan
10. [tail/tail] The Bluetones - Beat on the Brat
**rag**
1. [tail/tail] Embrace - If You Feel Like a Sinner
2. [tail/tail] James - Goal Goal Goal
3. [tail/tail] Ride - Performance
4. [tail/tail] Mansun - Mansun’s Only Live Song
5. [tail/tail] Embrace - Happy and Lost
6. [tail/tail] James - Wisdom of the Throat
7. [tail/tail] Supergrass - Sometimes We’re Very Sad
8. [tail/tail] Subcircus - She Ain’t Heavy
9. [tail/tail] Subcircus - Shelley's on the Telephone
10. [tail/tail] The La’s - I Did the Painting

### both-none
**rule_only**
1. [tail/tail] Mansun - An Open Letter to the Lyrical Trainspotter
2. [tail/tail] Mansun - Moronica
3. [tail/tail] Amplifier - Gateway
4. [tail/tail] Amplifier - Entity
5. [tail/tail] Dodgy - You Give Drugs a Bad Name
6. [tail/tail] Dodgy - Are You the One
7. [tail/tail] Yes - Ritual
8. [tail/tail] Liam Gallagher - Sad Song
9. [tail/tail] Liam Gallagher - Supersonic
10. [tail/tail] Пикник и Вадим Самойлов - Теперь ты
**rag**
1. [tail/tail] Mansun - Mansun’s Only Live Song
2. [tail/tail] James - Goal Goal Goal
3. [tail/tail] Supergrass - Sometimes We’re Very Sad
4. [tail/tail] King Crimson - Silent Night
5. [tail/tail] Spirit - Love Tonight
6. [tail/tail] Spirit - Monkey See Monkey Do
7. [tail/tail] Ash - Wild Surf (extended)
8. [tail/tail] Embrace - If You Feel Like a Sinner
9. [tail/tail] Procol Harum - Souvenir of London
10. [tail/tail] Uriah Heep - Wizard

### explore-0.1
**rule_only**
1. [tail/tail] Embrace - If You Feel Like a Sinner
2. [tail/tail] Embrace - Today
3. [tail/tail] Dodgy - You Give Drugs a Bad Name
4. [tail/tail] Dodgy - Are You the One
5. [tail/tail] Suede - What Am I Without You?
6. [tail/tail] Suede - Chalk Circles
7. [tail/tail] Paul Weller - Baptiste
8. [explore/mid] Babybird - If You'll Be Mine
9. [rule/head] Oasis - Columbia
10. [rule/head] Oasis - Digsy’s Dinner
**rag**
1. [tail/tail] Embrace - If You Feel Like a Sinner
2. [tail/tail] James - Goal Goal Goal
3. [tail/tail] Ash - Wild Surf (extended)
4. [tail/tail] Supergrass - Sometimes We’re Very Sad
5. [tail/tail] James - Wisdom of the Throat
6. [tail/tail] Embrace - A Tap on Your Shoulder
7. [tail/tail] Manic Street Preachers - If You Tolerate This
8. [explore/mid] Elbow - Strangeways to Holcombe Hill in 4.20
9. [semantic/head] Oasis - Supersonic
10. [semantic/head] Oasis - Digsy’s Dinner

### explore-0.5
**rule_only**
1. [tail/tail] Embrace - If You Feel Like a Sinner
2. [tail/tail] Embrace - Today
3. [tail/tail] Dodgy - You Give Drugs a Bad Name
4. [tail/tail] Dodgy - Are You the One
5. [tail/tail] Suede - What Am I Without You?
6. [tail/tail] Suede - Chalk Circles
7. [tail/tail] Paul Weller - Baptiste
8. [tail/tail] Paul Weller - Walkin’
9. [tail/tail] Ocean Colour Scene - Me, I'm Left Unsure
10. [tail/tail] Shed Seven - The Eye in the Sky
**rag**
1. [tail/tail] Embrace - If You Feel Like a Sinner
2. [tail/tail] James - Goal Goal Goal
3. [tail/tail] Ash - Wild Surf (extended)
4. [tail/tail] Supergrass - Sometimes We’re Very Sad
5. [tail/tail] James - Wisdom of the Throat
6. [tail/tail] Embrace - A Tap on Your Shoulder
7. [tail/tail] Manic Street Preachers - If You Tolerate This
8. [tail/tail] Mansun - Mansun’s Only Live Song
9. [tail/tail] Dodgy - Live Break
10. [tail/tail] Dodgy - This Is Ours

### explore-0.9
**rule_only**
1. [tail/tail] Embrace - If You Feel Like a Sinner
2. [tail/tail] Embrace - Today
3. [tail/tail] Dodgy - You Give Drugs a Bad Name
4. [tail/tail] Dodgy - Are You the One
5. [tail/tail] Suede - What Am I Without You?
6. [tail/tail] Suede - Chalk Circles
7. [tail/tail] Paul Weller - Baptiste
8. [tail/tail] Paul Weller - Walkin’
9. [tail/tail] Ocean Colour Scene - Me, I'm Left Unsure
10. [tail/tail] Shed Seven - The Eye in the Sky
**rag**
1. [tail/tail] Embrace - If You Feel Like a Sinner
2. [tail/tail] James - Goal Goal Goal
3. [tail/tail] Ash - Wild Surf (extended)
4. [tail/tail] Supergrass - Sometimes We’re Very Sad
5. [tail/tail] James - Wisdom of the Throat
6. [tail/tail] Embrace - A Tap on Your Shoulder
7. [tail/tail] Manic Street Preachers - If You Tolerate This
8. [tail/tail] Mansun - Mansun’s Only Live Song
9. [tail/tail] Dodgy - Live Break
10. [tail/tail] Dodgy - This Is Ours
