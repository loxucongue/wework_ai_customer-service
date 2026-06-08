from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StoreRecord:
    id: str
    name: str
    city: str
    address: str
    map_url: str = ""
    parking_name: str = ""
    parking_address: str = ""
    parking_link: str = ""
    business_hours: str = ""
    status_summary: str = "本地门店资料未包含暂停状态"
    is_public: bool = True


def local_store_records() -> list[StoreRecord]:
    return [
        StoreRecord(
            id="12",
            name="厦门思明店",
            city="厦门",
            address="厦门市思明区厦禾路1222号国骏大厦",
            map_url="https://mmapgwh.map.qq.com/shortlink/short?l=e526818bff583ca4a28cdf2eb6d0b899&tempSource=pcMap",
            parking_name="国骏大厦地下停车场",
            parking_address="福建省厦门市思明区厦禾路1168号",
            parking_link="https://mmapgwh.map.qq.com/shortlink/short?l=e526818bff583ca4a28cdf2eb6d0b899&tempSource=pcMap",
            business_hours="09:00-19:00",
        ),
        StoreRecord(
            id="385",
            name="厦门二店",
            city="厦门",
            address="厦门市湖里区湖里创新技术园嘉园大厦",
            map_url="https://mmapgwh.map.qq.com/shortlink/short?l=4c5285a342d450869ebc5ca83f86d3fe&tempSource=pcMap",
            parking_name="嘉园大厦停车场",
            parking_address="福建省厦门市湖里区安岭路与钟宅路交叉口西南60米",
            parking_link="https://mmapgwh.map.qq.com/shortlink/short?l=4a1d1e54db052935397ac7d621493b9b&tempSource=pcMap",
            business_hours="10:00-19:00",
        ),
        StoreRecord(
            id="386",
            name="厦门百星",
            city="厦门",
            address="厦门市湖里区枋湖西路189号",
            map_url="https://mmapgwh.map.qq.com/shortlink/short?l=4c5285a342d450869ebc5ca83f86d3fe&tempSource=pcMap",
            parking_name="嘉园大厦停车场",
            parking_address="福建省厦门市湖里区安岭路与钟宅路交叉口西南60米",
            parking_link="https://mmapgwh.map.qq.com/shortlink/short?l=4a1d1e54db052935397ac7d621493b9b&tempSource=pcMap",
            business_hours="10:00-19:00",
        ),
        StoreRecord(
            id="405",
            name="上海浦东二店",
            city="上海",
            address="上海市浦东新区杨高中路2108号FOR天物空间A栋",
            map_url="https://mmapgwh.map.qq.com/shortlink/short?l=14776fa967aad8a1aecf5059d4a415f4&tempSource=pcMap",
            parking_name="男龙总部园御龙宴会中心停车场",
            parking_address="上海市浦东新区南洋泾路578号（芳甸路地铁站3号口步行140米）",
            business_hours="10:00-19:00",
        ),
        StoreRecord(
            id="400",
            name="上海虹口店",
            city="上海",
            address="上海市虹口区花园路66号华博科技大厦",
            map_url="https://mmapgwh.map.qq.com/shortlink/short?l=f5334743dbe563dced62681169842a92&tempSource=pcMap",
            parking_name="华博科技大厦停车场",
            parking_address="上海市虹口区花园路66号华博科技大厦",
            business_hours="10:00-21:00",
        ),
        StoreRecord(
            id="322",
            name="上海嘉定店",
            city="上海",
            address="上海市嘉定区海波路366号点石矽金创意工坊",
            map_url="https://mmapgwh.map.qq.com/shortlink/short?l=a9dc89c22c641fb89df77afc09e9d049&tempSource=pcMap",
            parking_name="中星海兰馨停车场",
            parking_address="上海市嘉定区海波路中星海兰馨",
            business_hours="10:00-19:00",
        ),
        StoreRecord(
            id="fallback-xian-xiaozhai",
            name="西安小寨店",
            city="西安",
            address="陕西省西安市雁塔区小寨西路232号置地时代MOMOPARK7号楼",
            parking_name="MOMOPARK艺术购物中心地下停车场",
            business_hours="10:00-19:00",
            status_summary="当前资料未显示暂停营业状态",
        ),
        StoreRecord(
            id="fallback-xian-weiyang",
            name="西安未央店",
            city="西安",
            address="西安市未央区凤城二路经发大厦A座",
            parking_name="西安经发大厦A座地下停车场",
            business_hours="10:00-19:00",
            status_summary="当前资料未显示暂停营业状态",
        ),
        StoreRecord(
            id="fallback-xian-beilin",
            name="西安碑林店",
            city="西安",
            address="西安市碑林区体育馆东路宏信国际花园3号楼",
            parking_name="宏信国际花园停车场",
            business_hours="10:00-19:00",
            status_summary="当前资料未显示暂停营业状态",
        ),
        StoreRecord(
            id="20",
            name="西安中贸店",
            city="西安",
            address="",
            business_hours="10:00-20:00",
            status_summary="当前资料不是正常对外接待状态，建议以门店最新确认为准",
            is_public=False,
        ),
    ]
