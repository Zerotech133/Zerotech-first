import React,{useEffect, useState} from 'react'
import { Button, Select, DatePicker, Space, Input } from 'antd'
import './Timeline.scss'
import { getTimeline,getCaseId } from '@/api/timeline'
import * as echarts from 'echarts';import {
  TimelineParamType,
} from '@/interface/timeline'
import { Detail } from '../timeline/components/detail/Detail'

export const Timeline: React.FC = () => {
  // -----------------------------------------
  const { Search } = Input;
  const [spaceId,setSpaceId] = useState('')
  const onSearch = (value:any) => {
    setSpaceId(value)
    getCaseId({spaceId:value}).then(()=>console.log('导入成功'))
  }
  // -----------------------------------------
  const { Option } = Select;
  const { RangePicker } = DatePicker;
  // 类型
  const [typeList, setTypeList] = useState(['QQ聊天','微信聊天','通话','短信'])
  // 时间
  const [timeConditionType, setTime] = useState<any>('早于')
  // 时刻
  const [moment, setMoment] = useState<any>(null)
  const [timeCondition, setTimeCondition] = useState({
    conditionType:'',
    value:'',
  })
  // 明细数据
  const [detailsVisible, setDetailsVisible] = useState(false)
  const handleTypeChange =(type:any) => {
    setTypeList(type)
  }
  const handleTimeChange =(timeConditionType:any) => {
    setTime(timeConditionType)
  }
  // 时间区间
  function onChange(value: any, dateString: any) {
    setMoment(value)
    if (Array.isArray(dateString)) {
      const timeCondition = {
        conditionType: timeConditionType,
        value: dateString[0],
        value2: dateString[1]
      }
      setTimeCondition(timeCondition)
    } else {
      const timeCondition = {
        conditionType: timeConditionType,
        value: dateString
      }
      setTimeCondition(timeCondition)
    }
  }

  useEffect(() => {
    // 获取caseId
  })
  // 引入echarts
  const initEcharts = (QQChat:any,Wechat:any,message:any,call:any) => {  
    const Scatter = document.getElementById("Scatter") as HTMLElement;
    const MyEcharts = echarts.init(Scatter);
    const option = {
      // 标题
      title: {
        text: '时光轴'
      },
      legend: {
      },
      grid: {
        top: 30,
        left: 30,
      },
      // 图表提示信息
      tooltip: {
        show: true,
        trigger: 'item',
        confine: true,
      },
      dataZoom: [
        {
          type: 'inside'
        },
        {
          type: 'slider',
          filterMode: 'weakFilter',
          showDataShadow: false,
          bottom: 15,
          labelFormatter: ''
        },
      ],
      xAxis: {
          type: 'time',
          nameGap: 30,
          // min: "2011-12-01",
          // max: "2013-01-31",
          splitLine: {
            show: true
          },
          axisLine: {
            show: true
          }
      },
      yAxis: {
          type: 'category',
          show: false,
      },
      // 对端的名字
      series: [
        {
          name: 'QQ聊天',
          id: 'QQ聊天',
          data: QQChat,
          dimensions: ['日期', '类型+账户Id','对端名称','强度','ratio'],
          type: 'scatter',
          symbolSize: (params: any)=>{
            return params[4]*15
          },
          hoverAnimation: true,
          // selectedMode: 'single',
          tooltip: {
            trigger: 'item'
          },
          encode: {
            tooltip: ['日期','对端名称','强度']
          }
        },
        {
          name: '微信聊天',
          id: '微信聊天',
          label:true,
          data: Wechat,
          dimensions: ['日期', '类型+账户Id','对端名称','强度','ratio'],
          type: 'scatter',
          symbolSize: (params: any)=>{
            return params[4]*15
          },
          hoverAnimation: true,
          tooltip: {
            trigger: 'item'
          },
          itemStyle: {
            // normal: {
            //     opacity: 0.7
            // }
          },
          encode: {
              // x: [3, 1, 5],      // 表示维度 3、1、5 映射到 x 轴。
              // y: 2,              // 表示维度 2 映射到 y 轴。
              tooltip: ['日期','对端名称','强度'] // 表示维度 3、2、4 会在 tooltip 中显示。
          }
        },
        {
          name: '短信',  // 大小固定
          id: '短信',
          data: message,
          dimensions: ['日期', '类型+账户Id','对端名称','强度','ratio'],
          type: 'scatter',
          symbolSize: (params: any)=>{
            return params[4]*15
          },
          hoverAnimation: true,
          tooltip: {
            trigger: 'item'
          },
          itemStyle: {
            // normal: {
            //     opacity: 0.7
            // }
          },
          encode: {
              // x: [3, 1, 5],      // 表示维度 3、1、5 映射到 x 轴。
              // y: 2,              // 表示维度 2 映射到 y 轴。
              tooltip: ['日期','对端名称','强度'] // 表示维度 3、2、4 会在 tooltip 中显示。
          }
        },
        {
          name: '通话',
          id:'通话',
          data: call,
          dimensions: ['日期', '类型+账户Id','对端名称','强度','ratio'],
          type: 'scatter',
          symbolSize: (params: any)=>{
            return params[4]*15
          },
          hoverAnimation: true,
          tooltip: {
            trigger: 'item'
          },
          itemStyle: {
            // normal: {
            //     opacity: 0.7
            // }
          },
          encode: { 
            tooltip: ['日期','对端名称','强度'] // 表示维度 3、2、4 会在 tooltip 中显示。
          }
        }
      ]
    }
    MyEcharts.setOption(option);
    MyEcharts.on('click', function (params) {
      setDetailsVisible(true)
    })
  }

  // 查询
  const handleQuery = () => {
    const params: TimelineParamType = {
      spaceId: spaceId,
      typeList: typeList,
      timeCondition: timeCondition
    }
    // 处理接口数据
    let Wechat:any = []
    let QQChat:any = []
    let message:any = []
    let call:any = []
    getTimeline(params).then((res: any) => {
      res.data.forEach((item: any) => {
        // 根据类型分类
        switch(item.type) {
          case 'QQ聊天':
            QQChat.push(item)
            break;
          case '微信聊天':
            Wechat.push(item)
            break;
          case '短信':
            message.push(item)
            break;
          case '通话':
            call.push(item)
            break;
          }
        })
        QQChat = handleScatterData(QQChat)
        Wechat = handleScatterData(Wechat)
        message = handleScatterData(message)
        call = handleScatterData(call)
        initEcharts(QQChat,Wechat,message,call)
      })
  }

  // 处理成echarts所需的格式
  const handleScatterData = (arr:[]) => {
    let res:any = []
    const count: any = []
    arr.forEach((item:any) => {
      count.push(item.count)
    })
    const maxCount = count.sort((a:any,b:any)=> b-a)[0]
    arr.forEach((item:any,index) => {
      res[index] = []
      // [时间，对端账号，对端账号名]
      res[index].push(item.time, item.type + '_' + item.oppositeAccount, item.oppositeName, item.count, item.count/maxCount)
    })
    return res
  }

  // 清空数据
  const handleDelete = () => {
    setTypeList([])
    setTime(null)
    setMoment(null)
    setTimeCondition({
      conditionType:'',
      value:'',
    })
  }

  const toParent = (val:boolean) => {
    setDetailsVisible(val)
  }
  return(
    <div className="container">
      <div className='header'>
        {/* 类型 */}
        类型
        <Select
          value = {typeList}
          placeholder='请选择'
          mode="multiple"
          maxTagCount="responsive"
          onChange={handleTypeChange}
          style={{ minWidth: '10%',marginLeft:'10px',marginRight:'13%'}}>
          {/* 短信固定 */}
          <Option value="短信">短信</Option>  
          <Option value="微信聊天">微信聊天</Option>
          <Option value="QQ聊天">QQ聊天</Option>
          <Option value="通话">通话</Option>
        </Select>
        {/* 时间范围 */}
        时间
        <Select
          value={ timeConditionType }
          style={{marginLeft:'10px',marginRight:'1%'}}
          onChange={handleTimeChange}
          placeholder='请选择'>
          <Option value="早于">早于</Option>  
          <Option value="介于">介于</Option>
          <Option value="晚于">晚于</Option>
        </Select>
        {timeConditionType !== '介于' &&<DatePicker showTime={{ format: 'HH' }} format="YYYY-MM-DD HH" onChange={onChange} value={moment} />}
        {timeConditionType === '介于' &&<Space direction="vertical" size={12}>
          <RangePicker
            showTime={{ format: 'HH' }}
            format="YYYY-MM-DD HH"
            onChange={onChange}
            value={moment}
          />
      </Space>}
      <span className='btn'>
        <Button type="primary" onClick={handleQuery}>查询</Button>
        <Button style={{marginLeft:'30px'}} onClick={handleDelete}>清空条件</Button>
        <Search onSearch={onSearch} style={{ width: 120 }} />
      </span>
      </div>
      <div id='Scatter'></div>
      {detailsVisible && <Detail detailsVisible={ detailsVisible } toParent = { toParent } ></Detail>}
    </div>
  )
}