const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync(__dirname+'/../static/app.js','utf8').split('setInterval(pollAdmin,4000)')[0];
const app={innerHTML:''};const cls={toggle(){},add(){},remove(){}};
const document={querySelector:s=>s==='#app'?app:null,body:{classList:cls},documentElement:{style:{setProperty(){}},dir:'ltr',lang:'en'},createElement:()=>({className:'',innerHTML:'',querySelector(){return {onclick:null}},addEventListener(){}}),addEventListener(){},activeElement:{},title:'STAYOS'};
const ctx={document,window:{addEventListener(){},matchMedia(){return {matches:false}},navigator:{}},navigator:{language:'en-US',userAgent:'test'},location:{search:'',pathname:'/',hash:'',href:'http://localhost/',protocol:'http:',hostname:'localhost'},localStorage:{getItem:()=>null,setItem(){}},sessionStorage:{setItem(){}},history:{state:null,pushState(){},replaceState(){}},matchMedia:()=>({matches:false}),URLSearchParams,Date,console,setTimeout,clearTimeout,Intl,fetch:()=>{},requestAnimationFrame:()=>{},alert(){}};
vm.createContext(ctx);
vm.runInContext(src+`
adminSigned=true;hotelSettings={hotel_name:'AZURA RESORT',theme:'luxury',welcome:'Welcome'};
roomTypes=[{id:1,name:'Deluxe Garden',description:'Garden room',size:'42 m²',max_guests:3,image:'room-garden.webp'}];
hotelRooms=[{id:1,room:'204',room_type_id:1,room_type_name:'Deluxe Garden',floor:'2'}];
adminMenuItems=[{id:2,name:'Tropical Drink',name_he:'משקה טרופי',description:'Refreshing',description_he:'מרענן',price_cents:700,available:1,category:'drinks',image:'coconut.webp'}];
allActivities=[{id:2,title:'Yoga',description:'Sunrise',starts:new Date(Date.now()+3600000).toISOString(),ends:new Date(Date.now()+7200000).toISOString(),capacity:15,booked:0,location:'Pool Deck',price:10,image:'yoga.webp'}];
adminTab='reception';renderAdmin();`,ctx);
assert(app.innerHTML.includes('class="reception-layout"'));assert(app.innerHTML.includes('Room 204'));
vm.runInContext(`adminTab='rooms';renderAdmin();`,ctx);assert(app.innerHTML.includes('+ Add Room'));assert(app.innerHTML.includes('+ Add Room Type'));
vm.runInContext(`adminTab='catalog';renderAdmin();`,ctx);assert(app.innerHTML.includes('Tropical Drink'));assert(app.innerHTML.includes('+ Add Product'));
vm.runInContext(`adminTab='activities';renderAdmin();`,ctx);assert(app.innerHTML.includes('+ Add Activity'));assert(app.innerHTML.includes('Yoga'));
console.log('PASS admin UI generated for reception, rooms, menu, activities');
