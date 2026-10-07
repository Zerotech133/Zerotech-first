package com.qianjiangsoft.graphcore.service.impl;

import cn.hutool.core.bean.BeanUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.domain.entity.GraphCard;
import com.qianjiangsoft.graphcore.exception.BusinessException;
import com.qianjiangsoft.graphcore.mapper.GraphCardMapper;
import com.qianjiangsoft.graphcore.model.Card;
import com.qianjiangsoft.graphcore.param.card.CreateCardParam;
import com.qianjiangsoft.graphcore.service.SpaceCardService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/11/29 14:38
 */
@Service
public class SpaceCardServiceImpl implements SpaceCardService {

    @Autowired
    GraphCardMapper graphCardMapper;

    @Override
    public Card createCard(CreateCardParam param) {

        String spaceId = SpaceContextHolder.getSpaceId();

        //check name
        LambdaQueryWrapper<GraphCard> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(GraphCard::getName, param.getName())
                .eq(GraphCard::getSpaceId, spaceId);
        if(graphCardMapper.selectCount(queryWrapper) > 0) {
            throw new BusinessException(ResultCode.BUSINESS_DUPLICATE_RECORD_ERROR, "重复数据");
        }

        //插入数据
        GraphCard graphCard = BeanUtil.copyProperties(param, GraphCard.class);
        graphCard.setSpaceId(spaceId);
        graphCardMapper.insert(graphCard);

        return BeanUtil.copyProperties(graphCard, Card.class);
    }

    @Override
    public List<Card> selectCardListBySpaceId(String spaceId) {
        LambdaQueryWrapper<GraphCard> queryWrapper = new LambdaQueryWrapper();
        queryWrapper.eq(GraphCard::getSpaceId, spaceId);
        List<GraphCard> graphCardList = graphCardMapper.selectList(queryWrapper);
        return BeanUtil.copyToList(graphCardList, Card.class);
    }

    @Override
    public void deleteCardBySpaceId(String spaceId) {
        LambdaQueryWrapper<GraphCard> queryWrapper = new LambdaQueryWrapper();
        queryWrapper.eq(GraphCard::getSpaceId, spaceId);
        graphCardMapper.delete(queryWrapper);
    }
}
