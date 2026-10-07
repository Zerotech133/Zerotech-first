package com.qianjiangsoft.graphapi.controller;

import cn.hutool.core.date.DateField;
import cn.hutool.core.date.DatePattern;
import cn.hutool.core.date.DateTime;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.util.RandomUtil;
import cn.hutool.core.util.StrUtil;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import com.qianjiangsoft.graphapi.dto.TimeLineEventDTO;
import com.qianjiangsoft.graphcore.exception.BusinessException;
import com.qianjiangsoft.graphapi.param.QueryEventListParam;
import com.qianjiangsoft.graphapi.param.TimeLineEventParam;
import com.qianjiangsoft.graphapi.service.TimeLineService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.*;

/**
 * @author marshallwu
 * @date 2022/4/24 15:47
 */
@RequestMapping("/timeline")
public class TimeLineController {

    TimeLineService timeLineService;

    @PostMapping("/importEventData")
    void importEventData(@RequestBody List<TimeLineEventParam> paramList){
        timeLineService.importEventData(paramList);
    }


    @PostMapping("/importTestData")
    public void queryEventList(String spaceId) {

        if(StrUtil.isEmpty(spaceId)) {
            throw new BusinessException(ResultCode.PARAM_ERROR, "caseId参数丢失");
        }

        //清空数据
        timeLineService.clearCaseEventData(spaceId);

        List<TimeLineEventParam> result = new ArrayList<>();

        List<String> typeList = Arrays.asList("通话", "短信", "微信聊天","QQ聊天");

        for(int i=0; i<1000; i++) {
            TimeLineEventParam timeLineEventParam = new TimeLineEventParam();
            timeLineEventParam.setSpaceId(spaceId);
            timeLineEventParam.setType(RandomUtil.randomEle(typeList));

            int randomOpposite = RandomUtil.randomInt(1, 10);
            timeLineEventParam.setOppositeName("测试账号名_" + randomOpposite);
            timeLineEventParam.setOppositeAccount("测试对端ID_" + randomOpposite);

            //时间
            DateTime time = RandomUtil.randomDate(DateUtil.date(), DateField.HOUR_OF_DAY,0, 2400);
            timeLineEventParam.setTime(DateUtil.format(time, DatePattern.NORM_DATETIME_PATTERN));
            result.add(timeLineEventParam);
        }

        timeLineService.importEventData(result);
    }

    @GetMapping("/queryEventList")
    public List<TimeLineEventDTO> queryEventListForTest(QueryEventListParam param) {
        return timeLineService.queryEventList(param);
    }

    @PostMapping("/clearCaseEventData")
    void clearCaseEventData(String spaceId){
        timeLineService.clearCaseEventData(spaceId);
    }
}
