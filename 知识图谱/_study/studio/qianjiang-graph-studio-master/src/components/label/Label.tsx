import React from "react"
import {Avatar, Typography} from "antd";
import Image, {ImageType} from '../../assets/image'

type props = {
  label: string,
  keyValue: string,
  type: string
}

export const Label: React.FC<props> = ({label, keyValue, type}) => {
  const index = label.indexOf(keyValue)
  const length = keyValue.length
  let icon = Image[type as ImageType]

  return (<Typography>
    <Avatar src={`${icon}`} style={{width: 18, height: 18}}/> &nbsp;&nbsp;
    <Typography.Text>{label.substring(0, index)}</Typography.Text>
    <Typography.Text strong>{label.substring(index, index + length)}</Typography.Text>
    <Typography.Text>{label.substring(index + length, label.length)}</Typography.Text>
  </Typography>)
}
