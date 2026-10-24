"""GUARDIX welcome lines: original shayari, jokes and vibes (Hinglish). ~600 lines.

300 hand-written lines + 300 generated combinations.  Safe for groups (no abuse, no HTML characters).
Use  pick_line()  for a random line.  " // " inside a line = line break.
"""
import random

SHAYARI = """
Dil ki baat zubaan par aa hi jaati hai // jahan dost mil jaayein, mehfil saj hi jaati hai
Chaand sharmaaye, sitaare muskuraaye // jab is group mein ek naya dost aa jaaye
Raste alag the, manzil ek nikli // aaj tu is mehfil mein shaamil ho gaya yahi khushi nikli
Hasi ke bahaane dhoondhte the hum // tere aane se bahaane hazaar mil gaye
Khushiyon ki barsaat ho tujh par // is group ka har lamha yaadgaar ho tujh par
Dosti wo daulat hai jo kam nahi hoti // aur tere aane se ye mehfil kam nahi hoti
Shaam ki chai aur dosto ka saath // isse badi koi jannat nahi, bas yahi baat
Naye chehre se mehfil mein noor aaya // lagta hai aaj phir koi door se zaroor aaya
Dil se dil ka rishta yahin banta hai // dost jab milte hain, kaaravaan banta hai
Teri entry ne mehfil loot li // ab to har baat mein bas teri hi baat chali
Muskurahat tumhari, rounak hamari // dosti ki ye kahani ab shuru hai tumhari hamari
Kuch log zindagi mein khaas hote hain // aur kuch group mein aakar paas hote hain
Gham ko door rakho, hasi ko paas // is group mein sab milega, bas dosti ki aas
Subah ki pehli kiran jaisa swagat hai // dil se khush-aamdeed, ye apna ghar hai
Lafzon ki baarish ho ya khamoshi ka saaz // yahan har ek ki hai apni alag awaaz
Teri ek muskaan se din ban jaata hai // aise dost ka aana mehfil ko bhaata hai
Yaaron ki mehfil mein ek kursi khaali thi // tere aane se wo bhi aaj sajaa di
Chhoti si zindagi hai, hans ke jee lo // is group mein aaye ho, dil khol ke jee lo
Dosti ka rang sabse gehra hota hai // jahan bhi dost ho, wahan savera hota hai
Aaj ka din tere naam likh diya // is group ne tujhe apna maan liya
Waqt guzar jaata hai, yaadein reh jaati hain // chalo aaj kuch nayi yaadein banaate hain
Tere aane se rangat badal gayi // mehfil ki saari baatein sambhal gayi
Dil ki zameen par dosti ka beej // tere jaise dost se milta hai kuch aur hi tameej
Raat ki chaadar mein taare jagmagaye // jab tum jaise dost mehfil mein aaye
Sapno ki duniya mein ek naya sitaara // swagat hai tera, ye group hai tumhara
Chai ki pyaali mein jo mithaas hai // wahi mithaas tere is group mein aane mein hai
Har chehre ke peeche ek kahani hoti hai // aaj is mehfil mein teri kahani shuru hoti hai
Zindagi ka safar dosto se haseen hai // tere jaise hamsafar se aur bhi rangeen hai
Khuda ne jo dost diye, wo anmol hain // tere jaise log is duniya mein bemol hain
Mehfil mein tere aane ki khabar thi // tabhi to is group ki fizaa mein bahaar thi
Hansi ke phool khilte rahein // yaar tum humse milte rahein
Seedha dil se swagat tumhara // ab ye ghar bhi hua tumhara
Mausam bhi aaj thoda mehrbaan hai // kyunki group mein naya mehmaan hai
Dil ne kaha tum aaoge zaroor // dekho aa gaye, ab chhoti-si khushi ka suroor
Lamhe saare rangeen ho jaate hain // jab dost group mein aa jaate hain
Jahan dost wahan khushiyon ka mela // yaar tu aa gaya, ab mazaa hai double
Teri aamad se mehfil mein baharein hain // aur dosto ki nazrein tujh par thehri hui hain
Kuch rishte dil se jud jaate hain // jab hum is tarah group mein mil jaate hain
Khamosh sa tha ye group kal tak // tere aate hi hungama ho gaya, dekh sab
Pyaar bhare dil se, adab ke saath // swagat hai tumhara, hamare saath
Tu aaya to lagta hai din sunehra hai // dosti ka rang tujh par kuch aur gehra hai
Jo mile wahi dost bana lo // is group mein sabko apna bana lo
Muskuraao ki zindagi khoobsurat hai // aur is group mein tumhari zaroorat hai
Safar mein koi saathi mil jaye // to manzil ki raahein asaan ho jaati hain
Tu bhi apna hai, hum bhi tere hain // is group mein sab ek duje ke hain
Aankhon mein sapne, dil mein umang // chalo group mein karte hain thodi si tarang
Thoda hasna, thoda hasaana // yahi to hai is group ka fasaana
Naya dost, nayi baatein, nayi baat // aaj ki shaam ho gayi khaas
Dil ke darwaze khule hain tumhare liye // ye mehfil sajaai hai tumhare liye
Dosti ek ibaadat hai, nibhana seekho // is group mein sabko apna banana seekho
Tum aaye to rounak aayi // ek nayi si tazgi dil mein chhaayi
Har sunhari subah ek nayi umeed laati hai // jaise tu is group mein ek nayi khushi laaya hai
Chaand jaisa chehra, sitaaron jaisi baat // tere aane se roshan hui ye raat
Dil se dil mile to baat ban jaati hai // yahan har baat mein ek yaad ban jaati hai
Yaaron ka saath sabse bada tohfa hai // aur tu us tohfe ka naya hissa hai
Mubarak ho tujhe ye group ka saath // ab har din hoga khushiyon ki baat
Waqt ne dosto ko ek jagah laa khada kiya // tu bhi aa gaya, ab mela jam gaya
Kabhi hasna, kabhi rona, kabhi masti // dost wahi jo saath de har ek basti
Teri ek jhalak se mehfil khil uthi // jaise bagiya mein koi kali khil uthi
Jahan khushi hoti hai wahin dost hote hain // aur jahan dost hote hain wahin hum hote hain
Swagat ke phool bichhaaye hain raah mein // aao kadam rakho dosti ki panaah mein
Muskurahaton ka ye karwaan chalta rahe // tu bhi iska hissa ban, ye karwaan badhta rahe
Zindagi mein jo kuch bhi ho, dost saath ho // to har mushkil bhi ek mazaak ho
Naya safar, naye humsafar // swagat hai tera is group mein, dost bankar
Shabdon ki dori se rishte bunte hain // chalo is group mein kuch ache lamhe chunte hain
Mehfil saji hai, dil khula hai // tere aane se ye ghar bhara hai
Tere hone se mehfil mein jaan hai // tu hai to group ki alag hi shaan hai
Din bhar ki thakaan chhoo ho jaaye // jab group mein dost mil jaaye
Dosti ki raahon mein phool khilte rahein // hum sab yunhi hamesha milte rahein
Aaj is group ko ek taara mila // dosto ko ek aur pyaara dost mila
Khushi ke pal mein tera naam shaamil // tujhse hi to hai is mehfil ki manzil
Kal tak anjaan the, aaj apne ho gaye // is group mein aakar sab sapne ho gaye
Ek muskaan hazaar baatein kehti hai // tere aane ki khushi mein ye mehfil hans rahi hai
Dost mile to jaise sab kuch mil gaya // tu mila to lagta hai dil khil gaya
Tere naam ka chiraag jalaya hai // is group mein tera swagat ho aaya hai
Dil ki kitaab mein ek naya panna // tu likh de apna naam, ban ja humara apna
Raaston ne bhi kaha aaj ek mod aaya // group mein ek aur humsafar chala aaya
Safar lamba hai, saath chalte hain // is group mein naye rang bharte hain
Dosti ke is chaman mein phool khilaao // aao dost, ab saath muskuraao
Chhote chhote lamhon mein badi khushi hai // tere aane se ye mehfil aur bhi haseen hai
Aao baith ke baatein karein // dosti ke naye silsile ko aage badhayein
Naye dost ka swagat karna humara farz hai // tujhse milna is group ka sabse bada karz hai
Dil ki gehraai se swagat hai // tu humara hai, ye group tera ghar hai
Mehfil mein tere aane se roshni hui // jaise andhere mein ek shama jali
Muskuraana tumhari aadat banaye // aur hum sab tumhe yunhi hasate jaayein
Ye group ek parivaar hai pyaara // tu bhi iska hissa ban ja, ab hai tumhara
Gul khile, bulbul gaaye, shaam suhani ho // is group ki har baat ek kahani ho
Pyaar baantne se badhta hai, dosti nibhaane se // chal yaar shuru karte hain, is mehfil ke bahaane se
Mushkilein aati rahengi, hum saath hain // tu akela nahi, ab ye group tere saath hai
Zameen se aasmaan tak khushiyaan hon // is group mein har pal ki masti ho
Rooh ko sukoon mile dosto ke beech // aur dil ko sakoon mile yaaron ke beech
Aankhon ki chamak badhti hai // jab group mein dosti ki baat chalti hai
Kisi ki muskaan ki wajah ban jaana // yahi to hai is group ka asli khazaana
Jo dil se chaahe wo mil jaata hai // dost ho to har mod par khilkhilaata hai
Tere jaise dost kismat se milte hain // is group mein aakar phool khilte hain
Dosti ka matlab bas saath nibhana // tu is group mein mast reh, hasna aur hasaana
Aaj dil ne kaha ek nayi baat chhedein // tu aa gaya hai, ab dosti ka rang bhar dein
"""

JOKES = """
Teacher: Pani ka formula? Student: H2O. Teacher: Aur H2O ke baad? Student: Sir, pyaas lagti hai.
Dost: Tu itna soch kyun raha hai? Main: Soch raha hoon sochna band kar doon ya nahi, ye bhi soch raha hoon.
Doctor: Aapko aaram ki zaroorat hai. Patient: Doctor sahab, wo to Monday ko hota hai? Doctor: Matlab? Patient: Monday ko hamesha ghar pe aaram hota hai.
Wife: Kya hum kabhi bahar chalein? Husband: Haan, tum bahar chalo, main ghar sambhalta hoon.
Bhai ne kaha: Subah 5 baje uthunga. Alarm ne kaha: Main bhi bahut mehnat karta hoon.
Banta: Yaar mere paas ek idea hai. Santa: Kya? Banta: Wo idea hi mere paas hai, aur kuch nahi.
Student: Sir, exam mein kitne number se pass? Teacher: 33. Student: Sir, itna to mera hisaab bhi nahi hai.
Dost: Kal party hai! Main: Kal? Aaj kya ho gaya? Dost: Aaj bhi party hai, kal bhi party hai.
Mom: Padhai kar. Main: Bas ek reel aur. Mom: Ye 3 ghante pehle bhi bola tha.
Kisi ne kaha zindagi mein sab kuch ek baar milta hai. Maine kaha tab to meri neend kab milegi?
Papa: Kitne marks aaye? Beta: 90. Papa: Wah! Beta: Out of 400.
Chai: Main hoon. Neend: Main bhi hoon. Mere dimag ne kaha: Aap dono chale jao, Monday aa raha hai.
Dukandar: Sir ye mobile fall nahi hota. Customer: Fall nahi hota to thik hai, par mera kaam hi fall hone ka hai.
Dost: Tu bahut lucky hai. Main: Kyun? Dost: Kyunki tu mere jaisa dost paa gaya.
Teacher: Tum late kyun aaye? Student: Sir, raste mein board laga tha, School Ahead, Go Slow. Main slow chala.
Biwi: Aaj khaana kya banaun? Pati: Jo bhi bana do. Biwi: To phir bahar se mangwa lete hain.
Chhotu: Bhaiya, nind kaise aati hai? Bhaiya: Jab homework karte ho tab.
Wo bola: Main tumhari wajah se hanta hoon. Maine kaha: Sach? Wo bola: Haan, tumhari shakal dekh ke.
Maths ki class mein sirf ek sawaal tha: Hum yahan kya kar rahe hain?
WiFi ka password poochha, bola: Pehle pyaar to kar lo, phir dunga.
Alarm bajta hai: Utth jao. Main: 5 minute. Alarm: Theek hai. Time: 2 ghante baad.
Interviewer: Aap mein kya khaas baat hai? Candidate: Main kal se kaam shuru kar dunga. Interviewer: Kal se? Candidate: Haan, aaj to chhutti hai.
Friend: Tu bahut mast hai. Main: Shukriya. Friend: Par kaam kab karega? Main: Ye alag sawaal hai.
Bijli gayi, mobile ka charge gaya, kuch na raha. Bas ek hi cheez bachi: Group chat.
Teacher: Kal exam hai. Student: Aaj homework nahi mila kya? Teacher: Wahi to exam hai.
Gappu: Sunaa hai tum bahut tez chalte ho. Pappu: Haan, kyunki peeche kutta hai.
Ek aadmi ne mirror dekha aur bola: Wah, kitna smart banda hai. Mirror ne kaha: Bhai, main to sirf reflect karta hoon.
Doctor: Aapko sugar hai. Patient: Meri mithaas ki wajah se? Doctor: Nahi, test report ki wajah se.
Dost: Kab pahunchega? Main: Bas 5 minute. Dost: Ghar se nikla? Main: Ab nikal raha hoon.
Mummy: Chappal kahan hai? Main: Wo to... Mummy: Ab chappal wahi se aayegi jahan se wo udti hai.
Mere sapne bahut bade hain, par bed itna chhota ki pair bahar nikalte hain.
Dost: Shaadi kab kar raha hai? Main: Jab koi pasand karegi. Dost: To abhi tak koi nahi? Main: Wahi to problem hai.
Teacher: Do kam tees kitna hota hai? Student: Sir, do kam tees nahi hota, do kam tees hota hai fail.
Pappu: Bhaiya, ye kya hai? Dukaandar: Ye machhardani hai. Pappu: Aur ye? Dukaandar: Wo bhi machhardani. Pappu: Itni sari machhardani, machhar kahan hai?
Patni: Mere liye kya laaye ho? Pati: Wo hi jo tum chahti thi, dhairya.
Raat ko jab sab so jaate hain, tab meri soch jaag jaati hai, aur main dimaag se bolta hoon: Chal so ja.
Dost: Kaisa hai? Main: Theek hoon. Dost: Sach me? Main: Nahi, par sab aise hi bolte hain.
Chhoti si mushkil: Phone ki battery 1 percent. Badi mushkil: Charger bhi 1 percent.
Teacher: Tum paanch minute late ho. Student: Sir, aap bhi aaj late the. Teacher: Haan, par main teacher hoon. Student: To main late student hoon, brand alag hai.
Bhai, itna sundar group kahan milega? Yahin, is group mein, jo abhi tune join kiya.
Alarm band kar ke bola: Bas 10 minute. Aankh khuli to dopahar ke 2 baje.
Friend: Tere paas kitne paise hain? Main: Bas itne ki kisi ko udhaar na de sakoon.
Dukaandar: Bhaiya, kya chahiye? Main: Bas dekh raha hoon. Dukaandar: Dekhne ka bhi charge lagta hai. Main: To main chalta hoon.
Mama: Beta, bada hoke kya banega? Beta: Bada.
Chor ne kaha: Paise de do. Main ne kaha: Bhai mere paas kuch nahi. Chor: Fir main tujhe de deta hoon.
Banta: Meri biwi bahut khoobsurat hai. Santa: Kaise? Banta: Tasveer dekhne se pehle nahi dekhi thi.
Teacher: Aaj ka sabak yaad karo. Student: Sir, aaj ka sabak yaad hai, kal ka bhool gaya.
Dost ne poochha: Kya kar raha hai? Main ne kaha: Kuch nahi. Wo bola: Ye mera favourite kaam hai.
Papa: Itne mobile se kya milega? Beta: Papa, group chat mein sab milta hai, rishte bhi, dosti bhi.
Sab kehte hain paisa sab kuch nahi hota. Haan, par jo bhi hota hai paisa se hota hai.
Zindagi ek cricket match hai. Kabhi chakke, kabhi out, aur kabhi no ball par bhi out.
Meri life ka motto: Aaj ka kaam kal par, aur kal ka kaam parso par.
Dost bola: Tu bahut late hai. Main: Main late nahi, wo log early the.
Pyaar ek mithai hai, bas thoda sa kam khaoge to hi achha lagega.
Chai pe charcha, group pe baat, aur dost ke saath har rat.
Gharwale kehte hain: Dhyan se padho. Main kehta hoon: Dhyan to hai, par padhai kahan hai?
Biwi: Meri sari baatein suno. Pati: Main sun raha hoon. Biwi: To bol kya kaha? Pati: Wo sunne ka matlab tha, yaad rakhna nahi.
Ek baar ek aadmi ne apne dost se kaha: Tu bahut achha dost hai. Dost: Ye kehne ke liye 500 rupaye ka udhaar chahiye?
Internet ne duniya chhoti kar di, par mere phone ka storage utna hi bhar diya.
Dost: Kal milenge. Main: Pakka? Dost: Haan, par kal baad mein poochhna.
Hamari mehnat aur safalta ke beech ek hi cheez hai: Neend.
Alarm: Utth ja. Main: Dream mein 50 lakh mile the. Alarm: To wapas so ja, aur dream mein check kar.
Pehli baar group join kiya hai? Tension mat le, yahan sab pagal hain, tu bhi ban jayega.
Mom ne kaha: Phone chhod. Maine kaha: Group mein ek naya dost aaya hai. Mom ne kaha: Phir theek hai.
Doctor: Aapko aaram karna hoga. Patient: Doctor sahab, group chat chhodna padega kya? Doctor: Nahi nahi, wo to dawa hai.
Dost ne poochha: Kitni der se online hai? Main ne kaha: Jab se online hone ka mauka mila.
Santa: Mere paas ek solid idea hai. Banta: Wow! Kya? Santa: Wo bhi solid hai, par abhi yaad nahi.
Ek tha raja, ek thi rani. Dono ne group join kiya aur kahani yahin khatam ho gayi.
Ghar mein saare kehte hain neend se utho. Main kehta hoon: Pehle group mein good morning toh bol loon.
Dost: Tu paise kab lautayega? Main: Jab dono ko yaad aayega.
Maths: Do aur do chaar. Meri life: Do aur do paanch ho jaata hai kabhi kabhi.
Mummy: Padhai kar rahe ho? Main: Haan. Mummy: Kitab ulti hai. Main: Ye naya tareeka hai.
Wo bola: Tu bahut kamaal hai. Main bola: Kamal to phool hai, main to bas nasha hoon.
Raat ke 2 baje dimag bolta hai: Chal aaj sab kuch soch lete hain, kal ki tension aaj hi le lo.
Jab bhi koi poochta hai Kya kar rahe ho, main bolta hoon: Soch raha hoon sochna band karna hai.
Group mein sabse khaas kaun? Wo jo hamesha reply deta hai aur kabhi seen nahi chhodta.
Group chat ka sabse bada fayda: Akele nahi lagta, bas bore bhi nahi lagta.
Mere ghar mein WiFi hai, par signal wahi jahan main nahi hota.
Dost: Shopping karne chalein? Main: Haan. Dost: Kitna budget? Main: Dekhne ka, kharidne ka nahi.
Subah ki chai aur raat ka khaana, dono mere liye thoda sa zyada aana.
Kal mere dost ne kaha: Tu bahut change ho gaya hai. Main ne kaha: Haan, ab Monday ko bhi uth jaata hoon.
Aaj kal log kehte hain: Life mein tension nahi leni. Main kehta hoon: Ye bhi ek tension hai.
Doston ke saath maza aata hai, bina doston ke bhi maza aata hai, par woh maza aata hai jab dost bhi aata hai.
Mere phone mein 500 photos hain, aur sab mein sirf khaana hai.
Ek aadmi ne bola: Main kisi se nahi darta. Biwi ne awaaz di. Wo darr gaya.
Dost: Jaldi aa. Main: Aa raha hoon. Dost: Kahan hai? Main: Ghar pe.
Subah uth ke chai, phir WhatsApp, phir kaam. Kaam ki jagah thoda aur WhatsApp.
Jab group mein naya banda aata hai, sab bolte hain: Welcome. Aur mann mein sochte hain: Dekhte hain kitna chalta hai.
Dost ki pehchaan wahi, jo aapko group mein add kare aur bole: Bas ek minute mein sab ko jaan jaaoge.
Meri zindagi ka sabse bada raaz: Mujhe bhi nahi pata main kya kar raha hoon.
Teacher ne poochha: Dunia ka sabse tez kaun? Student: Sir, mera dost jab paise udhar maangta hai.
Ek mazedaar baat: Insan jab chhota hota hai tab bada hona chahta hai, aur bada hone ke baad chhota.
Mummy: Tu bahut sharmila hai. Main: Nahi mummy, bas group mein naya hoon.
Jab teacher poochta hai Koi sawaal? Pura class ek saath: Break kab hai?
Meri sabse badi takat: Kisi bhi bhookh mein khaana dhoondh lena.
Dost ne kaha: Chal party karte hain. Main ne poochha: Kiski? Wo bola: Jiski bhi ho, bas party ho.
Zindagi mein sab kuch fix hai, bas Monday ke baad Tuesday aata hai, par Sunday kabhi nahi aata.
Chhutti ka matlab: Aaj kuch nahi karna, kal bhi kuch nahi karna, parso Monday hai.
Tum bahut khaas ho. Kyunki tumne group mein join kiya hai, aur ab yahan se jaana mushkil hai.
Phone ka battery aur dost ka mood, dono kab down ho jaaye pata nahi chalta.
"""

VIBES = """
Swagat hai tumhara, ab ye group bhi tumhara ghar hai 🏡
Naya dost, nayi energy, nayi shuruaat. Welcome aboard! 🚀
Muskurao, kyunki tum ek aise group mein aaye ho jahan sab apne hain. 😊
Khushi baantne se badhti hai, aur is group mein bahut khushiyan hain. ✨
Aaj ka din tumhare liye ek naya adhyay hai, enjoy karo. 📖
Dosti ki duniya mein ek aur sitaara. Swagat hai! 🌟
Hasna mana nahi hai, yahan sab hasne aaye hain. 😄
Aaj se tum hamare parivaar ka hissa ho. Welcome home! 🤝
Hum sab ek dosto ki toli hain. Chalo jud jaao! 🎉
Yahan sab milenge: gyaan, masti, aur dher saari baatein. 💬
Ek nayi shuruaat, ek nayi kahani, aur ek naya dost. 🌈
Group mein aaye ho to dil khol ke baat karo, yahan koi paraya nahi. 💛
Har naya chehra hamare group ka noor hai. ✨
Aap aaye bahaar aayi, dil mein khushiyon ki sougaat chhai. 🌸
Mehfil ki jaan banoge tum, bas thoda baatein karo. 🎤
Aaj ka message: Aap special ho, kyunki aap yahan ho. 💎
Thoda hanso, thoda hasao, yahi to hai is group ka maza. 😆
Kabhi akela mat samajhna, yahan hazaron dost hain. 🫂
Dosti nibhao, mazaa lo, aur group ko apna ghar samjho. 🏠
Chai aur baatein, yahi hai hamaari shaan. ☕
Ek smile mein poori duniya jeet sakte ho, try karo. 😁
Yahan sab sunte hain, sab samajhte hain. Dil ki baat kehna. 🗣️
Welcome to the family! Yahan sab apne hain. 👨‍👩‍👧‍👦
Chalo dosto, ek aur member ka swagat karein! 👏
Naye ho? Koi baat nahi. Kuch hi din mein sab ke jaise ho jaaoge. 😎
Group rules padh lo, dosti nibha lo, aur masti kar lo. 📜
Aaj ka mantra: Positive socho, positive bolo, positive raho. 🌞
Yahan na koi chhota, na koi bada. Sab dost hain. 🤗
Har din ek naya mauka hai, aur aaj tumne ye group chuna. 🎯
Samay kam hai, masti zyada. Chalo shuru karein! ⏰
Hum koi perfect log nahi hain, bas dost hain. 💫
Koi baat dil pe mat lena, yahan sab mazaak mein hain. 😜
Aaj se tumhare chai ke saath hamari chat bhi chalegi. 💬
Hum aapke saath hain, aap bas group mein active rahiye. 💪
Is group ka ek hi rule: Sabki respect karo, sab tumhari respect karenge. 🙏
Khush raho, mast raho, aur group mein rehte raho. 🔥
Koi nayi baat? Koi mazaak? Yahan sab sunte hain. 👂
Zindagi mein hamesha koi dost ho jo group mein bhi saath de. 🤝
Chalo, aaj ka din yaadgaar bana dete hain. 🎈
Good vibes only. Is group mein sab positive hai. ☀️
Aao baat karein, kuch seekhein, kuch sikhayein. 📚
Naye safar ki shubhkamnayein. Group mein masti karte raho. 🛤️
Kuch log aate hain aur dil mein ghar kar lete hain. Tum bhi unmein se ho. ❤️
Yahan tumhe sab milega: dosti, hansi, aur thoda sa drama. 🎭
Aap aaye to rounak aayi. Welcome! 🎊
Is group ka sabse bada raaz: Yahan sab apne hain. 🤫
Dekho, koi bhi sawaal ho, puchh lena. Humein khushi hogi. 💡
Aaj ka din tumhare liye shubh ho. Group mein aaye ho, bas enjoy karo. 🌻
Tumhari entry ko 10 mein se 10. Welcome! 🏆
Shaant raho, mast raho, group mein vyast raho. 🧘
Swagat hai, ab tum hamare group ke VIP ho. 👑
Hum yahan baatein karne aaye hain, aur tum bhi. Chalo shuru karein. 🎙️
Jab dost milte hain tab har baat khaas ho jaati hai. 🌹
Ek dil se doosre dil tak, dosti ka pul bana lo. 🌉
Yahan time kab guzar jaata hai pata hi nahi chalta. ⌛
Aao baith jao, chai peeo, aur baatein karo. 🍵
Hum yahan hain, tum bhi ho, bas masti karo. 🎶
Group mein aaye ho to ek smile to banti hai. 😃
Aaj ka vichaar: Dost wahi jo bure waqt mein group mein bhi message kare. 💭
Hum sab ek hi kashti ke musafir hain. Chalo, saath chalein. ⛵
Naye dost banao, purane yaad rakho. 🧠
Pehle dost, phir baaki sab. Welcome to the squad! 🛡️
Yahan har baat ke liye ek jagah hai. Dil khol ke bolo. 🪄
Dosti mein sab maaf hai, par spam nahi. 😉
Mazaa aayega, bas thoda patience rakhna. 🍿
Ye group nahi, ye ek parivaar hai. 💞
Hansi se bada koi dawa nahi, aur group se bada koi parivaar nahi. 💊
Dil ko khush rakho, group ko khush rakho. 🥳
Aaj ka din shubh ho, aur group mein khushiyan barse. 🌧️
Dosti ka matlab saath, aur saath ka matlab group. 🫶
Chalo, ab to ek photo bhi banti hai! 📸
Welcome! Ab group ka mazaa double ho gaya. 🎁
Naya sapna, nayi udaan, naye dost. Chalo uden! 🕊️
Kuch cheezein sirf is group mein milti hain: sachchi dosti. 💎
Naye ho to sharmao mat. Pehla message likh do. 📝
Is group mein koi bhi apna hai. Bas hello bol do. 👋
Tum aaye ho to group mein bahaar aayi hai. 🌺
Wapas mat jaana, ab to maza shuru hua hai. 🎢
Mehfil ki jaan ban jao. Hum tumhara intezaar kar rahe the. 🎇
Jo bhi ho, group mein sab sambhal jaata hai. 🧩
Aaj ki shaam sirf tumhare naam. 🌆
Group ke rules mat todna, par dil zaroor jeet lena. 💘
Swagat hai tumhara. Ab ye group bhi tumhara. 🌍
Hum yahan baat karne aaye hain, tum bhi baat karo. 🗨️
Is duniya mein sabse mushkil kaam hai ek achha dost dhoondhna. Tumhe mil gaya! 🎯
Aaj se tumhari life mein group chat ka naya adhyay shuru. 📱
Chalo ek achhi si shuruaat karte hain. ☺️
Aaj ka din bada hi sundar hai. Group mein jo aaye ho tum. 🌤️
Hum yahan sirf dosti nibhaane aaye hain. Aur tum bhi. 🤍
"""

# ── generated combinations (300) ──
_OPEN = [
    "Welcome", "Swagat hai", "Khush-aamdeed", "Aapka dil se swagat", "Hello dost", "Namaste", "Salaam", "Sat Sri Akal",
    "Hey champ", "Aao aao", "Padhaariye", "Aaiye aaiye", "Arre wah", "Yo yo", "Hii hii", "Kya baat hai", "Mast entry",
    "Ye aaya apna banda", "Dil se welcome", "Bahut bahut swagat", "Aaj ka star aa gaya", "Hum intezaar kar rahe the",
    "Ek aur dost mil gaya", "Dosti ka naya member", "Naye mehmaan ka swagat",
]
_CLOSE = [
    "yahan sab apne hain, dil khol ke baat karo. 💛",
    "rules padh lena aur masti karte rehna. 📜",
    "tumhare aane se group ki shaan badh gayi. ✨",
    "ab ye ghar tumhara bhi hai. 🏡",
    "hansi, masti aur dosti, sab milega yahan. 😄",
    "bas respect do aur respect lo. 🙏",
    "koi bhi sawaal ho, bina jhijhak puchho. 💡",
    "chai ready hai, baatein shuru karo. ☕",
    "aaj se tum bhi is parivaar ka hissa ho. 🤝",
    "sab sunenge, bas apni baat rakho. 🎤",
    "mehfil ab poori hui. 🎉",
    "tension mat lo, yahan sab mast hain. 😎",
]
_GEN = [f"{o}! {c}" for o in _OPEN for c in _CLOSE]


def _parse(block):
    return [x.strip().replace(" // ", "\n") for x in block.strip().splitlines() if x.strip()]


LINES = _parse(SHAYARI) + _parse(JOKES) + _parse(VIBES) + _GEN


def pick_line():
    return random.choice(LINES)


if __name__ == "__main__":
    print("shayari", len(_parse(SHAYARI)), "| jokes", len(_parse(JOKES)), "| vibes", len(_parse(VIBES)),
          "| generated", len(_GEN), "| total", len(LINES), "| unique", len(set(LINES)),
          "| longest", max(len(x) for x in LINES), "| bad chars", [x for x in LINES if any(c in x for c in "<>&")] or "none")
