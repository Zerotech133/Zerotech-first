import React, { useState, useEffect, useCallback } from 'react'
import { Modal, Tree, Checkbox } from 'antd'
import { queryDataRangeApi } from '@/api/chart'
import { Key } from 'rc-tree/lib/interface';
import type { DataRangeINF } from '@/interface/chart-interface'
import type { CheckboxOptionType } from 'antd/lib/checkbox/Group'
import type { CheckboxValueType } from 'antd/es/checkbox/Group';
import  './DataRange.scss'
import store from '@/redux/store';
import { setCardData } from '@/redux/action';
import { CloseOutlined } from '@ant-design/icons';


type Props = {
  visible: boolean,
  showDataRange: (flag:boolean) => void
}

// 数据范围
const DataRange= ({visible, showDataRange}:Props) => {
  // 树结构
  const [treeData, setTreeData] = useState<DataRangeINF[]>([])
  // checkbox
  const [options, setOptions] = useState<CheckboxOptionType[]>([])
  // 选中树节点
  const [selectedKeys, setSelectedKeys] = useState<string[]>([])
  const [selectedTreeName, setSelectedTreeName] = useState<string>('')
  const [selectedBox, setSelectedBox] = useState<CheckboxValueType[]>([])
  const [selectedBoxName, setSelectedBoxName] = useState<string[]>([])

  // 点击树结构，右侧联动变化
  const getCheckOptions = useCallback((key: string, data: DataRangeINF[]) => {
    const option = data.filter((item: DataRangeINF) => item.key === key)[0].option
    setOptions(option)
  },[])

  // 接口数据处理
  const getData = (data: any) => {
    const datas =  data.map((item: any) => {
      const option = item.optionList.map((item: any) =>({
        label: item.name,
        value: item.value
      }))
      return {
        key: item.key,
        id: item.id,
        title: item.name,
        option
      }
    })
    return datas
  }
  
  const getDataRange = useCallback(() => {
    queryDataRangeApi().then((res: any) => {
      // 接口数据处理
      const data: DataRangeINF[] = getData(res.data)
      setTreeData(data)
      if (data.length === 0) return 
      const { key, title } = data[0]
      // 树结构初始化高亮
      setSelectedKeys([key])
      setSelectedTreeName(title)
      // 右侧checkbox初始化
      getCheckOptions(key, data)
    })
  },[getCheckOptions])

  // 打开弹框，接口请求
  useEffect(() => {
    if (visible) {
      getDataRange()
    }
  }, [visible, getDataRange])

  useEffect(() => {
    const arr = window.location.search.slice(1).split('&')
    let cardKey = ''
    let cardValueList: any = []
      
    arr.forEach(item => {
      let keyValue = item.split('=')
      let key = keyValue[0]
      let value = keyValue[1]
      if (key === 'cardKey'&&value) {
        setSelectedKeys([value])
        cardKey = value
      }
      if (key === 'cardValueList'&&value) {
        setSelectedKeys([value])
        cardValueList = value.split(',')
      }
    })
    store.dispatch(setCardData({cardData: {cardKey,cardValueList}}))
  }, [])

  // 点击树结构
  const selectTree = (selectedKeys: Key[]) => {
    getCheckOptions(selectedKeys[0] as string, treeData)
  }
  
  // box change事件
  const onChange = (checkedValues: CheckboxValueType[]) => {
    setSelectedBox(checkedValues)
    const checkboxName: string[] = options.filter((item:CheckboxOptionType) => checkedValues.includes(item.value))
                         .map((item:CheckboxOptionType) => item.label as string)
    setSelectedBoxName(checkboxName)
  }

  const handleOk = () => {
    store.dispatch(setCardData({cardData: {cardKey: selectedKeys[0],cardValueList: selectedBox}}))
    showDataRange(false)
  }

  const deleteAll = () => {
    store.dispatch(setCardData({cardData: {cardKey: '',cardValueList: []}}))
    setSelectedKeys([])
    setSelectedTreeName('')
    setSelectedBox([])
    setSelectedBoxName([])
  }

  return(
    <>
      <Modal 
      title="数据范围" 
      visible={visible}
      maskClosable={false}
      width={832}
      className="data-range-container"
      onCancel={() => {showDataRange(false)}}
      onOk= {handleOk}
      >
        <div className="content">
          <Tree treeData={treeData} selectedKeys={selectedKeys} onSelect={selectTree} />
          <Checkbox.Group options={options} value={selectedBox} onChange={onChange}/>
        </div>
      </Modal>
      {
        selectedBox.length && 
        <div className='data-range-text' >
          <span className='title'>数据范围:</span>
          <span className='detail'>
            <span>{selectedTreeName}:{selectedBoxName.join(',')}</span>
            <CloseOutlined  onClick={deleteAll}/>
          </span>
        </div>
      }
    </>
  );
}


export default DataRange